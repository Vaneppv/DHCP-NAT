# Imagen unica del laboratorio: Alpine + las herramientas que usan los
# contenedores (ip, iptables, tcpdump, dnsmasq, python3, curl...).
# Se construye una sola vez y luego arranca en segundos.
FROM alpine:3.20

RUN apk add --no-cache \
        iproute2 \
        iputils \
        iptables \
        tcpdump \
        dnsmasq \
        python3 \
        curl \
        bind-tools

WORKDIR /app
