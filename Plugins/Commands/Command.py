from typing import Union, Dict, List

from pathlib import Path
import json
import time

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.log import logger
from nonebot.params import CommandArg

from Scripts.Config import config
from Scripts.Managers import server_manager
from Scripts.Utils import Rules, turn_message, get_permission, get_args

def get_mc_log_config() -> Dict[str, str]:
    """读取ServerConfig.json中的mc_server_log_paths，返回{服务器名: 日志绝对路径}"""
# 这里很重要，麻烦搞自定义的腐竹别动这一块，不然这个功能直接废掉了。
    config_path = Path(__file__).resolve().parents[2] / "ServerConfig.json"
    try:
        if not config_path.exists():
            logger.warning(f"ServerConfig.json不存在，路径：{config_path}")
            return {}
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        mc_log_paths = config.get("mc_server_log_paths", {})
        return mc_log_paths if isinstance(mc_log_paths, dict) else {}
    except Exception as e:
        logger.warning(f"读取MC日志配置失败：{str(e)}")
        return {}

# 全局加载日志配置
MC_LOG_PATHS = get_mc_log_config()

# 日志读取配置（可微调）
# 以下给腐竹充分的自由发挥空间
CMD_LOG_LINES = 1  # 读最后几行日志，足够覆盖MC指令返回值，这个让腐竹自己设置
CMD_LOG_DELAY = 0.3  # 发指令后延时0.3秒读日志，等待写入
LOG_ENCODING = "utf-8"  # 如果是Windows乱码改gbk，反正只要不是乱码就行了

def read_mc_log_last_lines(server_name: str) -> List[str]:
    """读取指定服务器MC日志最后N行，返回行列表（方便按行解析）"""
    if server_name not in MC_LOG_PATHS:
        logger.warning(f"服务器[{server_name}]未配置日志路径，无法获取指令返回值")
        return []
    log_file = Path(MC_LOG_PATHS[server_name])
    if not log_file.exists():
        logger.warning(f"服务器[{server_name}]日志文件不存在：{MC_LOG_PATHS[server_name]}")
        return []
    try:
        with open(log_file, "r", encoding=LOG_ENCODING, errors="ignore") as f:
            lines = f.readlines()
        # 取最后CMD_LOG_LINES行，去除空行和换行符
        last_lines = [line.strip() for line in lines[-CMD_LOG_LINES:] if line.strip()]
        logger.success(f"服务器[{server_name}]读取日志最后{len(last_lines)}行")
        return last_lines
    except Exception as e:
        logger.warning(f"服务器[{server_name}]读取日志失败：{str(e)}")
        return []

def parse_mc_cmd_response(server_name: str, cmd: str) -> str:
    """解析MC指令返回值：发指令→延时→读日志→取最新非系统日志的返回内容"""
    # 延时等待日志写入
    time.sleep(CMD_LOG_DELAY)
    # 读取最新日志行
    log_lines = read_mc_log_last_lines(server_name)
    if not log_lines:
        return "日志读取失败，无法获取返回值"
    
    system_log_prefixes = [
        "[INFO]", "[WARN]", "[ERROR]", "[DEBUG]", "[FATAL]",
        "TPS from last", "MSPT", "Starting minecraft server", "Stopping server"
    ]
    for line in reversed(log_lines):
        if not any(line.startswith(prefix) for prefix in system_log_prefixes):
            return line if line else "指令执行成功，无返回内容"
    # 所有行都是系统日志，说明指令无返回/返回被系统日志覆盖
    return "指令执行成功，无可见返回值"

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
    # 全服务器执行
    if isinstance(response, dict):
        yield '命令已发送到所有服务器了喵，各服务器返回值：'
        server_list = list(response.items())
        for idx, (server_name, res) in enumerate(server_list):
            # 最后一个服务器用--，其余用|，主要就是好看没别的
            prefix = '  --' if idx == len(server_list)-1 else '  |'
            # 空返回值兜底
            res = res.strip() if res.strip() else '无任何返回内容'
            lines = res.split('\n')
            for line_idx, line in enumerate(lines):
                if line_idx == 0:
                    yield f'{prefix} [{server_name}]：{line}'
                else:
                    yield f'     {" " * len(server_name)}  ：{line}'
        return
    
    # 单服务器执行
    yield f'命令已发送到 [{name}] 了喵，返回值：'
    response = response.strip() if response.strip() else '无任何返回内容'
    for line in response.split('\n'):
        yield f'  -- {line}'

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


# async def execute_command(args: list):
    # if len(args) <= 1:
        # return False, '参数不正确！请查看语法后再试。'
    # server_flag, *command = args
    # if command := parse_command(command):
        # if server_flag == '*':
            # return True, await server_manager.execute(command)
        # if server := server_manager.get_server(server_flag):
            # return server.name, await server.send_command(command)
        # return False, F'服务器 [{server_flag}] 不存在！请检查插件配置。'
    # return False, F'命令 {command} 已被禁止！'
# 吐槽：这个获取返回值完全不可行，全是“命令已发送到服务器 [XXX]！服务器回应：命令已发送到服务器！当前插件不支持获取命令返回值。”
# 可以说打进去的命令完全没有回应，裂开了。
# 还是先保留一下万一原作者修了又能用呢？

async def execute_command(args: list):
    if len(args) <= 1:
        return False, '参数不正确！请查看语法后再试。'
    server_flag, *command = args
    # 解析并校验MC指令
    if not (command := parse_command(command)):
        return False, F'命令 {command} 已被禁止！'
    
    # 全服务器执行
    if server_flag == '*':
        all_responses = await server_manager.execute(command)
        # 遍历所有服务器，解析每个服务器的指令返回值
        cmd_responses = {}
        for server_name in all_responses.keys():
            if server := server_manager.get_server(server_name):
                cmd_responses[server_name] = parse_mc_cmd_response(server_name, command)
            else:
                cmd_responses[server_name] = "服务器未找到，无法获取返回值"
        return True, cmd_responses
    
    # 单服务器执行
    if server := server_manager.get_server(server_flag):
        await server.send_command(command)
        cmd_response = parse_mc_cmd_response(server.name, command)
        return server.name, cmd_response
    
    # 服务器不存在
    return False, F'服务器 [{server_flag}] 不存在！请检查插件配置。'
