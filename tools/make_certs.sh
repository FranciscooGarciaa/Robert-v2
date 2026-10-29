#!/usr/bin/env bash
# Local HTTPS for the PWA (the iPhone only lets a secure page use the microphone).
# Creates a small private CA and a server certificate for robert.local, the
# iPhone hotspot addresses and any of yours, in pi/certs/ (gitignored; deploy.sh copies it):
#   CERT_IPS="192.168.1.40" tools/make_certs.sh       (the Pi's fixed IP at home, if it has one)
# Install pi/certs/robert-ca.crt on the iPhone ONCE and trust it (see README).
set -euo pipefail
D="$(cd "$(dirname "$0")/.." && pwd)/pi/certs"
mkdir -p "$D" && cd "$D"
[ -f robert-ca.key ] || openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=Robert local CA" \
  -keyout robert-ca.key -out robert-ca.crt \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
SAN="DNS:robert.local,DNS:localhost,IP:127.0.0.1"
for ip in ${CERT_IPS:-}; do SAN="$SAN,IP:$ip"; done
for i in $(seq 2 14); do SAN="$SAN,IP:172.20.10.$i"; done   # iPhone hotspot range
openssl req -newkey rsa:2048 -nodes -subj "/CN=robert.local" -keyout server.key -out server.csr 2>/dev/null
printf "subjectAltName=%s\nextendedKeyUsage=serverAuth\nkeyUsage=digitalSignature,keyEncipherment\nbasicConstraints=CA:FALSE\n" "$SAN" > ext.cnf
# iOS rejects server certificates valid for more than 825 days.
openssl x509 -req -in server.csr -CA robert-ca.crt -CAkey robert-ca.key -CAcreateserial \
  -days 800 -extfile ext.cnf -out server.crt 2>/dev/null
rm -f server.csr ext.cnf
chmod 600 robert-ca.key server.key
echo "certs in $D"
