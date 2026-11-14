#!/usr/bin/env python3
"""Replay ambiguous Create outcomes against reference controllers over loopback HTTP."""
import argparse
import contextlib
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
from urllib.request import ProxyHandler, build_opener, Request


@dataclass(frozen=True)
class Case:
    name: str
    policy: str
    boundary: str
    identifiers: str = "named"
    hidden_reads: int = 0
    insert_foreign: bool = False


CASES = (
    Case("normal", "recover-named", "none"),
    Case("pending-write-ack-lost-stall", "stop", "pending-committed"),
    Case("pending-write-ack-lost-recover", "recover-named", "pending-committed"),
    Case("create-never-forwarded-stall", "stop", "create-not-forwarded"),
    Case("create-never-forwarded-recover", "recover-named", "create-not-forwarded"),
    Case("create-committed-stall", "stop", "create-committed"),
    Case("create-committed-adopt", "recover-named", "create-committed"),
    Case("create-committed-hidden-read", "recover-named", "create-committed", hidden_reads=1),
    Case("same-name-foreign-resource", "recover-named", "pending-committed", insert_foreign=True),
    Case("opaque-id-safeguard", "recover-named", "create-committed", "assigned"),
    Case("opaque-id-blind-retry", "retry-all", "create-committed", "assigned"),
)

TABLE = "sample-table"
SCHEMA = "partitionKey:S"
INTENT = "test-intent"
TIMEOUT = 10


class Fixture:
    def __init__(self, case, directory):
        self.case = case
        self.kube = directory / "kube.sqlite3"
        self.cloud = directory / "cloud.sqlite3"
        self.lock = threading.Lock()
        self.at_boundary = threading.Event()
        self.release = threading.Event()
        self.fault_drained = threading.Event()
        self.events = []
        self.hidden_reads = case.hidden_reads
        self.armed = True
        self.sql(self.kube, "CREATE TABLE managed (id INTEGER PRIMARY KEY, phase TEXT, external_id TEXT)")
        self.sql(self.kube, "INSERT INTO managed VALUES (1, 'new', NULL)")
        self.sql(self.cloud, "CREATE TABLE resources (id TEXT PRIMARY KEY, name TEXT, schema TEXT, intent TEXT)")

    @staticmethod
    def sql(path, statement, *params):
        with contextlib.closing(sqlite3.connect(path)) as connection, connection:
            return connection.execute(statement, params).fetchall()

    def snapshot(self):
        phase, external_id = self.sql(self.kube, "SELECT phase, external_id FROM managed")[0]
        inventory = [
            {"id": identity, "name": name, "schema": schema, "intent": intent}
            for identity, name, schema, intent in self.sql(
                self.cloud, "SELECT id, name, schema, intent FROM resources ORDER BY id"
            )
        ]
        return {"phase": phase, "external_id": external_id, "inventory": inventory}

    def store_resource(self, identity, intent):
        self.sql(self.cloud, "INSERT INTO resources VALUES (?, ?, ?, ?)", identity, TABLE, SCHEMA, intent)

    def event(self, name, **data):
        with self.lock:
            self.events.append({"seq": len(self.events) + 1, "event": name, **data})

    def pause(self, boundary):
        if self.armed and self.case.boundary == boundary:
            self.armed = False
            self.event("ack_withheld", boundary=boundary)
            self.at_boundary.set()
            if not self.release.wait(TIMEOUT):
                raise TimeoutError("supervisor did not release the fault gate")
            return True
        return False

    def handle(self, path, body):
        if path == "/managed/read":
            state = self.snapshot()
            return {key: state[key] for key in ("phase", "external_id")}
        if path == "/managed/pending":
            self.sql(self.kube, "UPDATE managed SET phase = 'pending' WHERE id = 1")
            self.event("pending_committed")
            return None if self.pause("pending-committed") else {}
        if path == "/managed/ready":
            self.sql(
                self.kube, "UPDATE managed SET phase = 'ready', external_id = ? WHERE id = 1",
                body["id"]
            )
            self.event("ready_committed", external_id=body["id"])
            return {}
        if path == "/cloud/describe":
            state = self.snapshot()
            match = next((row for row in state["inventory"] if row["id"] == body["id"]), None)
            if match is not None and self.hidden_reads:
                self.hidden_reads -= 1
                self.event("describe_hidden_committed_resource", external_id=body["id"])
                return {"resource": None}
            self.event("describe", external_id=body["id"], found=match is not None)
            return {"resource": match}
        if path == "/cloud/create":
            self.event("create_requested")
            if self.pause("create-not-forwarded"):
                self.event("create_discarded")
                return None
            inventory = self.snapshot()["inventory"]
            identity = TABLE if self.case.identifiers == "named" else f"assigned-{len(inventory) + 1}"
            if any(row["id"] == identity for row in inventory):
                self.event("create_conflict", external_id=identity)
                return {"conflict": True}
            self.store_resource(identity, INTENT)
            self.event("create_committed", external_id=identity)
            return None if self.pause("create-committed") else {"id": identity}
        raise ValueError(f"Unknown fixture route {path}")


def request_handler(fixture):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            body = json.loads(self.rfile.read(length))
            result = fixture.handle(self.path, body)
            if result is None:
                fixture.fault_drained.set()
                return
            payload = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass
    return Handler


def matches_intent(resource):
    return resource["name"] == TABLE and resource["schema"] == SCHEMA and resource["intent"] == INTENT


def worker(port, policy, identifiers):
    # A fresh process can inspect only its API surface. The supervisor owns the
    # independent read of both durable stores.
    opener = build_opener(ProxyHandler({}))

    def post(path, **body):
        data = json.dumps(body).encode()
        request = Request(f"http://127.0.0.1:{port}{path}", data=data, method="POST")
        with opener.open(request, timeout=TIMEOUT) as response:
            return json.load(response)

    state = post("/managed/read")
    if state["phase"] == "ready":
        return "already-ready"
    if state["phase"] == "pending":
        if policy == "stop" or (policy == "recover-named" and identifiers == "assigned"):
            return "blocked-ambiguous"
    if identifiers == "named":
        known = post("/cloud/describe", id=TABLE)["resource"]
        if known is not None:
            if not matches_intent(known):
                return "blocked-unverified"
            post("/managed/ready", id=known["id"])
            return "observed-existing"
    post("/managed/pending")
    outcome = post("/cloud/create")
    if outcome.get("conflict"):
        # A conflict alone cannot prove ownership. Require a matching read.
        known = post("/cloud/describe", id=TABLE)["resource"]
        if known is None or not matches_intent(known):
            return "blocked-unverified"
        post("/managed/ready", id=known["id"])
        return "observed-after-conflict"
    post("/managed/ready", id=outcome["id"])
    return "created"


def run_case(case):
    with tempfile.TemporaryDirectory(prefix="create-ack-") as temp:
        fixture = Fixture(case, Path(temp))
        with ThreadingHTTPServer(("127.0.0.1", 0), request_handler(fixture)) as server:
            server.daemon_threads = False
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
            thread.start()
            command = [
                sys.executable, str(Path(__file__).resolve()), "_worker", str(server.server_port),
                case.policy, case.identifiers
            ]
            try:
                cut = None
                if case.boundary != "none":
                    fixture.event("worker_started", attempt=1)
                    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
                        try:
                            if not fixture.at_boundary.wait(TIMEOUT):
                                raise TimeoutError(f"fault gate not reached: {case.name}")
                            cut = fixture.snapshot()
                            fixture.event("oracle_at_cut", **cut)
                            proc.kill()
                            proc.communicate(timeout=TIMEOUT)
                            if proc.returncode != -9:
                                raise RuntimeError(f"expected SIGKILL, got {proc.returncode}")
                            fixture.event("worker_killed", attempt=1, signal="SIGKILL")
                        finally:
                            if proc.poll() is None:
                                proc.kill()
                            fixture.release.set()
                    if not fixture.fault_drained.wait(TIMEOUT):
                        raise TimeoutError("interrupted HTTP handler did not finish")
                    if case.insert_foreign:
                        fixture.store_resource(TABLE, "different-intent")
                        fixture.event("foreign_resource_committed", external_id=TABLE)
                    result = run_worker(command, fixture, attempt=2)
                else:
                    result = run_worker(command, fixture, attempt=1)
            finally:
                server.shutdown()
                thread.join()
        final = fixture.snapshot()
        fixture.event("oracle_at_end", **final)
        return {"case": case.name, "boundary": case.boundary, "policy": case.policy,
                "identifiers": case.identifiers, "cut": cut, "final": final,
                "worker_result": result, "verdict": verdict(final), "events": fixture.events}


def run_worker(command, fixture, attempt):
    fixture.event("worker_started", attempt=attempt)
    completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=TIMEOUT)
    outcome = completed.stdout.strip()
    fixture.event("worker_result", attempt=attempt, outcome=outcome)
    return outcome


def verdict(state):
    inventory = state["inventory"]
    if state["phase"] == "ready":
        exact = [row for row in inventory if row["id"] == state["external_id"]
                 and matches_intent(row)]
        if len(inventory) > 1:
            return "extra-resource"
        return "converged" if len(exact) == 1 else "false-ready"
    if state["phase"] == "pending":
        if not inventory:
            return "blocked-before-external-create"
        if any(matches_intent(row) for row in inventory):
            return "blocked-with-external-resource"
        return "blocked-with-foreign-resource"
    return "incomplete"


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "_worker":
        _, _, port, policy, identifiers = sys.argv
        print(worker(int(port), policy, identifiers), flush=True)
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=[case.name for case in CASES])
    parser.add_argument("--output", type=Path, help="Write the complete JSON replay trace.")
    args = parser.parse_args()
    results = [run_case(case) for case in CASES if not args.case or case.name == args.case]
    for result in results:
        print(f"{result['case']:38} {result['verdict']}")
    if args.output:
        args.output.write_text(json.dumps({"system": "reference-fixture", "runs": results}, indent=2) + "\n")


if __name__ == "__main__":
    main()
