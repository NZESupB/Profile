Daily.list(https://raw.githubusercontent.com/NZESupB/Profile/main/Rule/Daily.list) CDN加速(https://raw.nxnow.top/NZESupB/Profile/main/Rule/Daily.list) 低流量日常网站 

Direct.list(https://raw.githubusercontent.com/NZESupB/Profile/main/Rule/Direct.list) CDN加速(https://raw.nxnow.top/NZESupB/Profile/main/Rule/Direct.list) 国内漏网之鱼直连&部分误杀恢复

Download.list(https://raw.githubusercontent.com/NZESupB/Profile/main/Rule/Download.list) CDN加速(https://raw.nxnow.top/NZESupB/Profile/main/Rule/Download.list)需要大带宽下载的地址

GameDirect.list(https://raw.githubusercontent.com/NZESupB/Profile/main/Rule/GameDirect.list) CDN加速(https://raw.nxnow.top/NZESupB/Profile/main/Rule/GameDirect.list) 游戏直连规则或者自动分配国内cdn地址直连

Games.list(https://raw.githubusercontent.com/NZESupB/Profile/main/Rule/Games.list) CDN加速(https://raw.nxnow.top/NZESupB/Profile/main/Rule/Games.list) 游戏代理规则,节点需要开启udp

Speedtest.list(https://raw.githubusercontent.com/NZESupB/Profile/main/Rule/Speedtest.list) CDN加速(https://raw.nxnow.top/NZESupB/Profile/main/Rule/Speedtest.list) 测速节点,不过不太准,fast由于是Netflix提供,走Netflix策略

jp.list(https://raw.githubusercontent.com/NZESupB/Profile/main/Rule/jp.list) CDN加速(https://raw.nxnow.top/NZESupB/Profile/main/Rule/jp.list) 为锁日本ip的域名

us.list(https://raw.githubusercontent.com/NZESupB/Profile/main/Rule/us.list) CDN加速(https://raw.nxnow.top/NZESupB/Profile/main/Rule/us.list) 为锁美国ip的域名或者一些服务器位于美国的网站

tw.list(https://raw.githubusercontent.com/NZESupB/Profile/main/Rule/tw.list) CDN加速(https://raw.nxnow.top/NZESupB/Profile/main/Rule/tw.list) 为锁台湾ip的域名

Video.list(https://raw.githubusercontent.com/NZESupB/Profile/main/Rule/Video.list) CDN加速(https://raw.nxnow.top/NZESupB/Profile/main/Rule/Video.list) Emby和一些其他的需要代理的影视站



## Mihomo 自动化

共享 `.list` 保持文本规则；同文件的等价覆盖可以清理，跨策略覆盖保留。正式 MRS 编译与验证通过 GitHub Actions 完成，入口及依赖说明见 [Shell/README.md](../Shell/README.md)。

- `build.yaml`：本地规则与模板 provider 的映射、明确排除项及上游格式。
- `upstreams.lock.json`：不可变上游地址、原始发布快照和 SHA-256。
- `snapshots/`：Sukka 流媒体、下载域名集、局域网及 Telegram IP 的原始发布文本，保留来源与许可证头，供可重复构建使用；与根目录自定义规则分开维护。
- `Torrent.list`、`tw.list`：编译产物但不新增当前模板路由。
- `fakeipfillter.txt`：DNS 通配模式来源，单独接入 fake-ip-filter，不转换为分流 MRS。
- `Speedtest.list`：USER-AGENT 供其他客户端使用，在 Mihomo 产物中按清单明确排除，其余未知格式会阻止构建。

构建生成的去重报告记录来源行号、覆盖依据和保留原因。上游差量只用于同一运行包，不回写共享规则文件。

当前构建模板为 `Template/Meta.yaml`。人工维护 `.list` 分组、`build.yaml` 的来源/策略映射和锁文件后，Actions 自动生成 Mihomo MRS/classical 及 Clash、Surge、Surfboard、Shadowrocket、Quantumult X 规则。默认分支成功构建后同步到独立 `rules` 分支，功能分支只验证并上传 artifact。

固定 MRS 同时校验同版文本与完整 classical 补充，关键词和正则不会因使用纯域名 MRS 而丢失。OpenAI 补充来源中的 AS20473 通过清单显式展开为锁定 CIDR；不保留运行时 Geodata 依赖。客户端不支持的逻辑、正则、进程或 no-resolve 等规则列入 `clients/report.json`，不会静默改写匹配范围。

规则片段需要目标客户端已有相应策略组；按 `clients/report.json` 的 `policy_names` 对接。单 URL 的 MRS/文本/YAML 接入与完整使用方法见 [Shell/README.md](../Shell/README.md)。
