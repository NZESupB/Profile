#!/bin/bash
tyblue()                           #天依蓝
{
    echo -e "\\033[36;1m${*}\\033[0m"
}

get_acme()
{
      cmd1="--issue -d $domain  --standalone"
      cmd2="--installcert  -d  $domain  --key-file   /etc/ssl/$domain.key   --fullchain-file /etc/ssl/$domain.cer"
      /root/.acme.sh/acme.sh  $cmd1
      /root/.acme.sh/acme.sh  $cmd2
}
ask_if()
{
    local choice=""
    while [ "$choice" != "y" ] && [ "$choice" != "n" ]
    do
        tyblue "$1"
        read choice
    done
    [ $choice == y ] && return 0
    return 1
}

installacme()
{
      wget -O -  https://github.nxnow.xyz/https://raw.githubusercontent.com/acmesh-official/acme.sh/master/acme.sh | sh -s -- --install-online -m  $email
}

apt install wget -y
apt install socat
echo "注册域名的邮箱"
read email
if ask_if "您输入的邮箱为:"$email",是否继续？(y/n)" 
then installacme
else
 exit 0
fi
echo "申请证书的域名"
read domain
if ask_if "您输入的域名为:"$domain",是否继续？(y/n)"
then get_acme
else
 exit 0
fi
echo "证书已生成,请在/etc/ssl/下查看" 
