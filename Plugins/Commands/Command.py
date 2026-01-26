# 与Status.py使用一样的逻辑
# 纯纯的复制粘贴，本人捞B

from typing import Union, Dict, List
from pathlib import Path
import json
import sys
import subprocess
import re
import asyncio

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.log import logger
from nonebot.params import CommandArg

from Scripts.Config import config
from Scripts.Managers import server_manager
from Scripts.Utils import Rules, turn_message, get_permission, get_args

import mcrcon
# ====================== 完全复制status.py的自动安装依赖逻辑 ======================
def auto_install_deps():
    """
    自动检测并安装mcrcon依赖（和status.py完全一致）
    未安装则通过pip自动下载，安装失败则打印错误并退出
    """
    try:
        import mcrcon
        logger.info("Command.py：检测到mcrcon依赖已安装，跳过自动安装")
    except ImportError:
        logger.warning("Command.py：未检测到mcrcon依赖，开始自动安装...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "mcrcon", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            import mcrcon
            logger.success("Command.py：mcrcon依赖自动安装成功！")
        except subprocess.CalledProcessError:
            logger.critical("Command.py：mcrcon依赖自动安装失败！请手动执行 pip install mcrcon 后重启程序")
            sys.exit(1)
        except Exception as e:
            logger.critical(f"Command.py：自动安装依赖时发生未知错误：{str(e)}，请手动安装mcrcon")
            sys.exit(1)

auto_install_deps()

def get_mc_rcon_config() -> Dict[str, dict]:
    config_path = Path(__file__).resolve().parents[2] / "ServerConfig.json"
    try:
        if not config_path.exists():
            logger.critical(f"Command.py：ServerConfig.json不存在，路径：{config_path}")
            return {}
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        mc_rcon = config.get("mc_server_rcon", {})
        if not isinstance(mc_rcon, dict):
            logger.error(f"Command.py：mc_server_rcon格式错误，当前：{type(mc_rcon)}")
            return {}
        # 补全默认配置
        for srv_name, rcon_info in mc_rcon.items():
            rcon_info.setdefault("port", 25575)  # MC默认RCON端口
            rcon_info.setdefault("timeout", 5)   # 默认5秒超时
        logger.success(f"Command.py：成功读取MC RCON配置，共{len(mc_rcon)}台服务器：{list(mc_rcon.keys())}")
        return mc_rcon
    except json.JSONDecodeError:
        logger.critical(f"Command.py：ServerConfig.json不是合法的JSON格式")
        return {}
    except Exception as e:
        logger.critical(f"Command.py：读取ServerConfig.json失败：{str(e)}")
        return {}

# 全局加载RCON配置
MC_RCON_CONFIG = get_mc_rcon_config()

def clean_rcon_response(response: str) -> str:
    if not response:
        return ""
    # 过滤颜色符和特殊符号（和status.py的正则一致）
    clean_resp = re.sub(r'§[0-9a-fA-Za-z]|◴', '', response)
    return clean_resp.strip()

async def rcon_execute(server_name: str, cmd: str) -> str:
    # 校验服务器是否配置RCON（和status.py一致的兜底逻辑）
    if server_name not in MC_RCON_CONFIG:
        logger.warning(f"Command.py：服务器[{server_name}]未配置RCON信息，无法执行指令")
        return f"服务器[{server_name}]未配置RCON信息"
    rcon_info = MC_RCON_CONFIG[server_name]

    def sync_rcon_operation():
        try:
            with mcrcon.MCRcon(
                host=rcon_info["host"],
                password=rcon_info["password"],
                port=rcon_info["port"],
                timeout=rcon_info["timeout"]
            ) as rcon:
                resp = rcon.command(cmd)  # 执行RCON指令，获取原始返回值
                return clean_rcon_response(resp)  # 过滤颜色符，返回整洁结果
        except Exception as e:
            logger.warning(f"Command.py：服务器[{server_name}]RCON执行失败：{str(e)}")
            return f"RCON执行失败：{str(e)[:50]}"

    try:
        timeout = rcon_info["timeout"] + 1
        result = await asyncio.wait_for(
            asyncio.to_thread(sync_rcon_operation),
            timeout=timeout
        )
        logger.info(f"Command.py：服务器[{server_name}]RCON执行成功，指令：{cmd}")
        return result if result else "指令执行成功，无返回内容"
    except asyncio.TimeoutError:
        logger.warning(f"Command.py：服务器[{server_name}]RCON操作超时（{timeout}秒）")
        return f"RCON操作超时（{timeout}秒），服务器无响应"
    except Exception as e:
        logger.warning(f"Command.py：服务器[{server_name}]执行指令失败：{str(e)}")
        return f"指令执行失败：{str(e)[:50]}"

logger.debug('加载命令 Command 完毕！')
matcher = on_command('command', force_whitespace=True, rule=Rules.command_rule)

@matcher.handle()
async def handle_group(event: GroupMessageEvent, args: Message = CommandArg()):
    if not get_permission(event):
        await matcher.finish('你没有权限执行此命令！')
    flag, response = await execute_command(get_args(args))
    if flag is False:
        await matcher.finish(response)
    message = turn_message(command_handler(flag, response))
    await matcher.finish(message)

def command_handler(name: str, response: Union[str, dict]):
    if isinstance(response, dict):
        yield '命令已发送到所有服务器了喵，各服务器返回值：'
        server_list = list(response.items())
        for idx, (server_name, res) in enumerate(server_list):
            prefix = '  └─' if idx == len(server_list)-1 else '  ├─'
            res = res.strip() if res.strip() else '无任何返回内容'
            lines = res.split('\n')
            for line_idx, line in enumerate(lines):
                if line_idx == 0:
                    yield f'{prefix} [{server_name}]：{line}'
                else:
                    yield f'     {" " * len(server_name)}  ：{line}'
        return
    yield f'命令已发送到 [{name}] 了喵，返回值：'
    response = response.strip() if response.strip() else '无任何返回内容'
    for line in response.split('\n'):
        yield f'  └─ {line}'

def parse_command(command: list):
    command = ' '.join(command)
    if config.command_minecraft_whitelist:
        for enabled_command in config.command_minecraft_whitelist:
            if command.startswith(enabled_command):
                return command
        return None
    for disabled_command in config.command_minecraft_blacklist:
        if command.startswith(disabled_command):
            return None
    return command

async def execute_command(args: list):
    if len(args) <= 1:
        return False, '参数不正确！示例：/command * tps（全服）\n/command <服务器名称> <命令>（单服）'
    server_flag, *command = args
    if not (command := parse_command(command)):
        return False, f'命令 {command} 已被黑白名单禁止！'
    # 无RCON配置直接返回
    if not MC_RCON_CONFIG:
        return False, '未读取到任何服务器RCON配置，请检查ServerConfig.json的mc_server_rcon节点'
    
    # 情况1：全服务器执行
    if server_flag == '*':
        cmd_responses = {}
        for server_name in MC_RCON_CONFIG.keys():
            cmd_responses[server_name] = await rcon_execute(server_name, command)
            logger.info(f"Command.py：全服执行 → [{server_name}] 指令：{command} → 结果：{cmd_responses[server_name][:30]}")
        return True, cmd_responses
    
    # 情况2：单服务器执行
    target_server = None
    for server_name in MC_RCON_CONFIG.keys():
        if server_flag == server_name or server_flag in server_name:
            target_server = server_name
            break
    if not target_server:
        return False, f'服务器 [{server_flag}] 未找到！已配置RCON的服务器：{list(MC_RCON_CONFIG.keys())}'
    
    # 异步执行RCON指令并获取结果
    response = await rcon_execute(target_server, command)
    logger.info(f"Command.py：单服执行 → [{target_server}] 指令：{command} → 结果：{response[:30]}")
    return target_server, response
