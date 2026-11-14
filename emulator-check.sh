set -eu
id=$(docker run --rm -d -p 127.0.0.1::8000 amazon/dynamodb-local@sha256:6ada631ec7aa5fc83446855eb17233e3dad184fec47477e8140c6c569cfe0c86 -jar DynamoDBLocal.jar -inMemory -sharedDb)
trap 'docker stop "$id" >/dev/null' EXIT
url=http://$(docker port "$id" 8000/tcp)
call() {
  curl -fsS --aws-sigv4 aws:amz:us-east-1:dynamodb -u fixture:fixture -H "X-Amz-Target: DynamoDB_20120810.$1" -H 'Content-Type: application/x-amz-json-1.0' -d "$2" "$url"
}
for i in $(seq 1 20); do
  if call ListTables '{}' 2>/dev/null; then break; fi
  sleep 1
done
printf '\n'
call CreateTable '{"TableName":"ack-baseline","AttributeDefinitions":[{"AttributeName":"id","AttributeType":"S"}],"KeySchema":[{"AttributeName":"id","KeyType":"HASH"}],"BillingMode":"PAY_PER_REQUEST"}'
printf '\n'
call DescribeTable '{"TableName":"ack-baseline"}'
printf '\n'
call ListTables '{}'
printf '\n'