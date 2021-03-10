!bin/bash
'127.0.0.1       soga.sprov.xyz' | tee -a /etc/hosts
'127.0.0.1       doc.sprov.xyz' | tee -a /etc/hosts
bash < <(curl -Ls https://www.sspanel.nl/soga/install/install.sh)
