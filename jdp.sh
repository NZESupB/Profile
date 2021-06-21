Authfile=$JD_DIR/config/auth.json
Serverfile=$PanelPath/server.js
PanelPath=$JD_DIR/panel

#判断auth.json文件是否存在
if [ ! -f "$Authfile" ];then
echo "auth.json文件缺失，创建auth.json文件..."
touch $Authfile
echo '{"user":"admin","password":"adminadmin"}' > $Authfile
echo "auth.json文件创建成功！"
fi

#启动网页终端
if [[ $ENABLE_WEB_PANEL == true ]]; then
    if [[ $ENABLE_TTYD == true ]]; then
    echo -e "======================== 启动网页终端 ========================\n"
        pm2 id ttyd
        ttyd_check_results=$(pm2 id ttyd)
        if [[ $ttyd_check_results == "[]" ]]; then
            ## 增加环境变量
            export PS1="\u@\h:\w $ "
            pm2 start ttyd --name="ttyd" -- -t fontSize=14 -t disableLeaveAlert=true -t rendererType=webgl bash

            if [[ $? -eq 0 ]]; then
                echo -e "网页终端启动成功...\n"
            else
                echo -e "网页终端启动失败，但容器将继续启动...\n"
            fi
        else
        echo -e "网页终端已经启动了.\n"
        fi
    elif [[ $ENABLE_TTYD == false ]]; then
        echo -e "已设置为不自动启动网页终端，跳过...\n"
    fi
else
    echo -e "已设置为不自动启动控制面板，因此也不启动网页终端...\n"
fi

#启动控制面板
echo -e "======================== 启动控制面板 ========================\n"
if [[ $ENABLE_WEB_PANEL == true ]]; then
    pm2 id server
    server_check_results=$(pm2 id server)
    if [[ $server_check_results == "[]" ]]; then
        pm2 start $Serverfile
        if [[ $? -eq 0 ]]; then
            echo -e "控制面板启动成功...\n"
            echo -e "如未修改用户名密码，则初始用户名为：admin，初始密码为：adminadmin\n"
            echo -e "请访问 http://<ip>:5678 登陆并修改配置...\n"
        else
            echo -e "控制面板启动失败或控制面板已经启动了，容器将继续启动...\n"
        fi
    else
    echo -e "控制面板已经启动了.\n"
    fi
elif [[ $ENABLE_WEB_PANEL == false ]]; then
    echo -e "已设置为不自动启动控制面板，跳过...\n"
fi
