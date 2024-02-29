#!/bin/sh
# 0 4 15 */1 * bash /root/cleanLog.sh >> /var/log/cleanLog.log 2>&1
echo "======== 准备清理Docker日志 ========"

logs=$(find /var/lib/docker/containers/ -name *-json.log)

for log in $logs
        do
                echo "clean logs : $log"
                cat /dev/null > $log
        done

echo "======== 清理Docker日志完成 ========"

# 清理超过50M的内核日志
echo "======== 准备清理内核日志 ========"
journalctl --vacuum-size=50M
echo "======== 清理内核日志完成，大于50MB的日志文件已移除 ========"

# 仅保留一周的内核日志
#journalctl --vacuum-time=1w

# 清理其他日志
echo "======== 准备清理历史日志 ========"
find /var/log -type f -name "*.gz" -delete
echo "======== 清理历史日志完成 ========"
# 清理Docker无效文件
echo "======== 准备清理Docker无效镜像和文件 ========"
docker system prune -a -f
echo "======== 清理清理Docker无效镜像和文件完成 ========"

