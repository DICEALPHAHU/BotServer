# Minecraft_QQBot

### [**机器人配置文档**](https://mcbot.ytb.icu/)

原作者仓库：[**点此快捷跳转**](https://github.com/Minecraft-QQBot/BotServer)

## 面向Minecraft服务器的QQ机器人

**一款基于 Nonebot2 用多种方式与 Minecraft 交互的 Python QQ 机器人**。功能丰富，使用简单，性能高强且可以自行配置，仅需简单配置即可使用。目前已实现的功能有：

- 多服互联，群服互通。
    - 在不同服务器之间转发消息。
    - 可在游戏内看到 QQ 群的消息。
    - 可使用指令在游戏内向 QQ 群发送消息。
    - 可播报服务器开启、关闭，玩家进入离开服务器以及死亡消息。
- 使用 WebUi 简单配置。
- **配置MC的rcon，直接查询服务器状态情况（本Fork分支机器人额外功能）**。
- **戳一戳机器人发送MC冷知识（本Fork分支机器人额外功能）**。
- **机器人自动读取B站up最新动态（本Fork分支机器人额外功能）**
- 可自行配置指令的开启或关闭。
- 可自行配置接入 AI 功能。
- 对 QQ 群指令相应。目前已实现的指令有：
    - `luck` 查看今日幸运指数。
    - `mcdr` 在指定的服务器上执行 MCDR 指令。
    - `list` 查询每个服务器的玩家在线情况。
    - `help` 查看帮助信息。
    - `server` 查看当前在线的服务器并显示对应编号，也可用于查看服务器占用。
    - `bound` 有关绑定白名单的指令。
    - `command` 发送指令到服务器，**获取命令返回值。（本Fork分支机器人额外修复功能）**。
    - `weather` **查询当日的天气，给出门的你一点点小的参考。（本Fork分支机器人额外功能）**
    - `cq` **抽签娱乐功能，给自己来点运气。（本Fork分支机器人额外功能）**
    - `早上/中午/晚上吃什么` **不知道吃什么，选择困难症，让机器人帮你选择。（本Fork分支机器人额外功能）**
    - `抽老婆` **让机器人给群员来个CP。（本Fork分支机器人额外功能）**



> [!TIP]
> 若遇到问题，或有更好的想法，可以加入 QQ 群 [`原作者群962802248`](https://qm.qq.com/q/B3kmvJl2xO) [`体素创艺1040011516`](http://qm.qq.com/cgi-bin/qm/qr?_wv=1027&k=cEcK_tG_vB7wgFz8XZ8Owj2ZnnmwvYNS&authKey=FWUya6ympAeAq19pIs15t8B7dHr212a7sdNEgj7CHodLxxlebvyp31YOtKVGc6J1&noverify=0&group_code=1040011516)或者提交 Issues
> 有任何问题请反馈。若你有能力，欢迎为本项目提供代码贡献！

## 友链

- [湖南科技大学体素创艺组织](https://www.mp-gamer.com/hnustmc)
- [湖南大学岳麓幻境社](https://www.hmomc.cn/)

## 版权与协议声明
1. **开源协议**：本项目基于 GNU General Public License v3.0 (GPL-3.0) 协议开源，完整协议文本见项目根目录下的 LICENSE 文件。
2. **项目溯源**：
   - 本项目派生自（fork）：[BotServer]，原项目仓库地址：[原项目GitHub链接](https://github.com/Minecraft-QQBot/BotServer)；
   - 原项目版权归 [Lonely-Sails团队] 所有（© 2024 [Lonely-Sails团队]）。
3. **修改贡献**：
   - 本仓库为原项目的修改版本，DICEALPHAHU 对代码进行了核心修改，全部修改记录可通过GitHub提交历史追溯：https://github.com/DICEALPHAHU/BotServer/commits/main
4. **使用规范**：
   - 任何个人/组织使用、修改、分发本项目代码，均需遵守GPL3.0协议要求：保持代码开源、协议一致，且需标注本项目/原项目的溯源信息；
   - 本项目允许商用/收费，但收费版本的代码需同步开源，不得闭源分发。
