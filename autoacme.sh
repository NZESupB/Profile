#!/bin/bash
tyblue()
{
    echo -e "\\033[36;1m${*}\\033[0m"
}
get_acme()
{
      #cmd1="--issue -d $domain --standalone"
      cmd1="--issue -d $domain --nginx"
      cmd2="--install-cert  -d  $domain  --key-file   /etc/ssl/$domain.key   --fullchain-file /etc/ssl/$domain.cer   --reloadcmd "nginx -s reload""
      /root/.acme.sh/acme.sh  $cmd1
      /root/.acme.sh/acme.sh  $cmd2
      #nginx -c /etc/nginx/nginx.conf
      #echo "nginx使用默认配置启动"
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
      if ask_if "y使用acme默认安装,n使用其他方式安装(y/n)"
       then bash <(curl  https://get.acme.sh | sh -s email=$email)
      else
       wget -O -  https://github.nzesupb.workers.dev/https://raw.githubusercontent.com/acmesh-official/acme.sh/master/acme.sh | sh -s -- --install-online -m  $email
      fi
      #bash <(curl  https://get.acme.sh | sh -s email=$email)
      #wget -O -  https://github.nzesupb.workers.dev/https://raw.githubusercontent.com/acmesh-official/acme.sh/master/acme.sh | sh -s -- --install-online -m  $email
      #wget -O -  https://github.nzesupb.workers.dev/https://raw.githubusercontent.com/acmesh-official/acme.sh/master/acme.sh | sh -s email=$email
      #crontab -l > conf && echo "50 0 * * * \"/root/.acme.sh\"/acme.sh --cron --home \"/root/.acme.sh\" > /dev/null" >> conf && crontab conf && rm -f conf
      cmdupdate="--upgrade  --auto-upgrade"
      /root/.acme.sh/acme.sh   $cmdupdate
}
input_Domain()
{
      #nginx -s stop
      echo "申请证书的域名"
      read domain
      if ask_if "您输入的域名为:"$domain",是否继续？(y/n)"
       then get_acme
      else
       exit 0
      fi
}
input_Email()
{
      apt install nginx -y
      apt install wget -y
      apt install socat
      echo "注册域名的邮箱"
      read email
      installacme
}

ask_if "是否第一次运行此脚本？(y/n)" && input_Email
input_Domain
echo "证书已生成,请在/etc/ssl/下查看" 
