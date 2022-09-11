FROM alpine:latest
RUN apk update && apk add python3 py3-pip
RUN python3 -m pip install pika requests boto3
COPY . /app
WORKDIR /app
EXPOSE 8080