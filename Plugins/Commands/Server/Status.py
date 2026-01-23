from io import BytesIO
from os.path import exists

from matplotlib import pyplot
from matplotlib.font_manager import findSystemFonts, FontProperties
from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment, Message
from nonebot.log import logger
from nonebot.params import CommandArg

from Scripts import Globals
from Scripts.Managers import server_manager
from Scripts.Utils import Rules, turn_message

from typing import Dict, Tuple  # 新增这行，解决Dict/Tuple未定义的问题
from pathlib import Path  # 核心：导入Path，解决NameError
import json
import re
import time
import asyncio

CMD_DELAY = 0.2  
LOG_ENCODING = "utf-8"  
# 吐槽：原作者，其实除了CPU和RAM，服务器应该还需要读取TPS和MSPT这个重要的数据。
# 光看前两者占用是看不出毛病的说实话，毕竟直接能反应的就是TPS和MSPT。
# 加TPS和MSPT的原理如下：我之前打算说在服务器端里面直接获取内部的调用方法的，
# 我才接触python一个月都不到，个人Python太垃圾不知道怎么搞，
# 索性直接调用服务器的日志，读最近的10行，然后打印，输出，抓关键词，
# 感谢原作者提供的command思路，让我这个垃圾编程有点用处了说实话。
# 这种方法，其实只适用于Purpur端（或许paper端也能用？），因为我在Purpur端试的，其他端的指令输出可能都不一样。
# 甚至，如果有些服务器一秒钟的日志量很大的话，这个局限性其实也很大，
# 读不到数据就会出错（这个是当时在湖大幻境社开发群里就有人说了，我当时没想这么多）。
# 个人捞B，懒得做适配了，代码和我有一个能跑就行了，搞那么复杂干啥。
# 20260123糊糊留。

# ====================== 读取ServerConfig.json中的MC日志路径 ======================
def get_mc_log_config() -> Dict[str, str]:
    """
    读取BotServer/ServerConfig.json中的mc_server_log_paths配置
    返回：{服务器名: 日志绝对路径}
    """
    # 定位ServerConfig.json：BotServer根目录
    # status.py路径：BotServer/Plugins/Commands/Server/status.py
    config_path = Path(__file__).resolve().parents[3] / "ServerConfig.json"
    try:
        if not config_path.exists():
            logger.critical(f"ServerConfig.json不存在，路径：{config_path}")
            return {}
        # 读取JSON配置
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        # 提取mc_server_log_paths，无则返回空
        mc_log_paths = config.get("mc_server_log_paths", {})
        if not isinstance(mc_log_paths, dict):
            logger.error(f"mc_server_log_paths格式错误，必须是对象，当前：{type(mc_log_paths)}")
            return {}
        logger.success(f"成功读取MC日志配置，共{len(mc_log_paths)}台服务器：{list(mc_log_paths.keys())}")
        return mc_log_paths
    except json.JSONDecodeError:
        logger.critical(f"ServerConfig.json不是合法的JSON格式")
        return {}
    except Exception as e:
        logger.critical(f"读取ServerConfig.json失败：{str(e)}")
        return {}

# 全局加载MC日志配置（启动时加载一次，无需重复读取）
MC_LOG_PATHS = get_mc_log_config()

# ====================== 读取MC服务器最新日志最后10行（其实按理来说应该做行数的自定义配置的，我懒） ======================
def read_mc_log(server_name: str) -> str:
    """根据服务器名，读取对应MC日志最后10行，返回日志文本"""
    if server_name not in MC_LOG_PATHS:
        logger.warning(f"服务器[{server_name}]未配置日志路径")
        return ""
    log_file = Path(MC_LOG_PATHS[server_name])
    if not log_file.exists():
        logger.warning(f"服务器[{server_name}]日志文件不存在：{MC_LOG_PATHS[server_name]}")
        return ""
    try:
        with open(log_file, "r", encoding=LOG_ENCODING, errors="ignore") as f:
            lines = f.readlines()
        # 取最后10行，避免读全文件
        last_lines = lines[-10:] if len(lines) >= 10 else lines
        logger.success(f"服务器[{server_name}]成功读取日志最后{len(last_lines)}行")
        return "".join(last_lines)
    except Exception as e:
        logger.warning(f"服务器[{server_name}]读取日志失败：{str(e)}")
        return ""


# ======================解析TPS/MSPT======================
def parse_tps_from_log(log_content: str) -> float:
    """
    解析TPS - 适配格式：TPS from last 5s, 1m, 5m, 15m: 20.0, 20.0, 20.0, 20.0
    返回：1分钟TPS值（第二个数值），失败返回0.0
    """
    tps = 0.0
    # 精准匹配TPS格式正则
    tps_pattern = re.compile(r"TPS from last 5s, 1m, 5m, 15m:\s*(\d+\.\d+),\s*(\d+\.\d+)")
    # 找到所有匹配的TPS行，取最后一行（最新指令的返回）
    all_matches = tps_pattern.findall(log_content)
    if all_matches:
        last_match = all_matches[-1]  # 取最后一次匹配的结果
        tps = round(float(last_match[1]), 1)
        logger.success(f"解析到最新TPS（最后一次匹配）：{tps}")
    else:
        logger.warning(f"最新10行日志中未找到TPS格式")
    return tps

def parse_mspt_from_log(log_content: str) -> float:
    """
    解析MSPT - 适配格式：9.0/7.0/10.9, 9.1/7.0/15.9, 8.6/6.4/20.3
    返回：1分钟平均MSPT值（第三个段第一个数值），失败返回0.0
    """
    mspt = 0.0
    # 精准匹配MSPT格式正则
    mspt_pattern = re.compile(r"(\d+\.\d+)/\d+\.\d+/\d+\.\d+,\s*(\d+\.\d+)/\d+\.\d+/\d+\.\d+,\s*(\d+\.\d+)/\d+\.\d+/\d+\.\d+")
    # 找到所有匹配的MSPT行，取最后一行（最新/mspt指令的返回）
    all_matches = mspt_pattern.findall(log_content)
    if all_matches:
        last_match = all_matches[-1]  # 取最后一次匹配的结果
        mspt = round(float(last_match[2]), 1)
        logger.success(f"解析到最新MSPT（最后一次匹配）：{mspt}ms")
    else:
        logger.warning(f"最新10行日志中未找到MSPT格式")
    return mspt

async def get_tps_mspt(server_name: str) -> Tuple[float, float]:
    """主函数：输入服务器名，返回解析后的(TPS, MSPT)"""
    try:
        server = server_manager.get_server(server_name)
        if not server:
            logger.warning(f"服务器[{server_name}]未找到，无法发送指令")
            return 0.0, 0.0
        # 串行发指令：先tps→等日志写入→再mspt→再等日志写入，彻底解决websocket冲突
        await server.send_command("tps")    # 先发/tps，串行执行无并发
        await asyncio.sleep(CMD_DELAY/2)   # 短延时，等tps日志写入
        await server.send_command("mspt")   # 再发/mspt，前一个指令执行完再执行
        await asyncio.sleep(CMD_DELAY/2)   # 再短延时，等mspt日志写入
        # 不留延迟的话，可能日志挤到一坨，会读不出来
        logger.success(f"服务器[{server_name}]串行发送/tps、/mspt指令成功，均已等待日志写入")
    except Exception as e:
        logger.warning(f"服务器[{server_name}]发/tps指令失败：{str(e)}")
    await asyncio.sleep(CMD_DELAY)  # 延时等待日志写入，复用原有配置
    log_content = read_mc_log(server_name)
    if not log_content:
        return 0.0, 0.0
    tps = parse_tps_from_log(log_content)
    mspt = parse_mspt_from_log(log_content)
    logger.info(f"服务器[{server_name}]最终解析结果：TPS={tps} | MSPT={mspt}ms")
    return tps, mspt 
        
def choose_font():
    for font_format in ('ttf', 'ttc'):
        if exists(f'./Font.{font_format}'):
            logger.info(F'已找到用户设置字体文件，将自动选择该字体作为图表字体。')
            return FontProperties(fname=f'./Font.{font_format}', size=15)
    for font_path in findSystemFonts():
        if 'KAITI' in font_path.upper():
            logger.success(F'自动选择系统字体 {font_path} 设为图表字体。')
            return FontProperties(fname=font_path, size=15)


font = choose_font()
matcher = on_command('server status', force_whitespace=True, block=True, priority=5, rule=Rules.command_rule)


@matcher.handle()
async def handle_group(event: MessageEvent, args: Message = CommandArg()):
    if args := args.extract_plain_text().strip():
        flag, response = await get_status(args)
        if flag is False:
            await matcher.finish(response)
        # 调用解析函数获取TPS/MSPT
        tps, mspt = await get_tps_mspt(flag)
        message = turn_message(detailed_handler(flag, response))
        await matcher.finish(message)
    flag, response = await get_status()
    if flag is False:
        await matcher.finish(response)
    # logger.error(response) 也不知道当初加这个干啥，搞得服务器控制台报ERROR
    # 批量解析所有服务器TPS/MSPT
    tps_mspt_data = {}
    for server_name in response.keys():
        tps_mspt_data[server_name] =await get_tps_mspt(server_name)
    message = turn_message(status_handler(response, tps_mspt_data))
    await matcher.finish(message)


def status_handler(data: dict, tps_mspt_data: dict = None):
    yield '已连接的所有服务器信息：'
    for name, occupation in data.items():
        yield F'————— {name} —————'
        if occupation:
            cpu, ram = occupation
            yield F'  内存使用率：{ram:.1f}%'
            yield F'  CPU 使用率：{cpu:.1f}%'
            # 展示TPS/MSPT
            if tps_mspt_data and name in tps_mspt_data:
                tps, mspt = tps_mspt_data[name]
                yield F'  TPS（1分钟）：{tps:.1f}'
                yield F'  MSPT（1分钟平均）：{mspt:.1f}ms'
            continue
        yield F'  此服务器未处于监视状态！'
    if font is None:
        yield '\n由于系统中没有找到可用的中文字体，无法显示中文标题。请查看文档自行配置！'
        return None
    if not any(data.values()):
        yield '\n当前没有服务器处于监视状态！无法绘制柱状图。'
    yield '\n所有服务器的占用柱状图：'
    yield str(MessageSegment.image(draw_chart(data)))
    return None


def detailed_handler(name: str, data: list):
    cpu, ram = data
    yield F'服务器 [{name}] 的详细信息：'
    yield F'  内存使用率：{ram:.1f}%'
    yield F'  CPU 使用率：{cpu:.1f}%'
    # 展示TPS/MSPT
    yield F'  TPS（1分钟）：{tps:.1f}'
    yield F'  MSPT（1分钟平均）：{mspt:.1f}ms'
    if image := draw_history_chart(name):
        yield '\n服务器的占用历史记录：'
        yield str(MessageSegment.image(image))
        return None
    yield '\n未找到服务器的占用历史记录，无法绘制图表。请稍后再试！'
    return None


def draw_chart(data: dict):
    count, names = 0, []
    cpu_bar, ram_bar = None, None
    logger.debug('正在绘制服务器占比柱状图……')
    pyplot.xlabel('Percentage(%)', loc='right')
    pyplot.title('Server Usage Percentage')
    for name, occupation in data.items():
        if occupation:
            pos = (count * 2)
            cpu, ram = occupation
            names.append(name)
            cpu_bar = pyplot.barh(pos, cpu, color='red')
            ram_bar = pyplot.barh(pos + 1, ram, color='blue')
            count += 1
    pyplot.legend((cpu_bar, ram_bar), ('CPU', 'RAM'))
    pyplot.yticks([(count * 2 + 0.5) for count in range(len(names))], names, fontproperties=font)
    buffer = BytesIO()
    pyplot.savefig(buffer, format='png')
    pyplot.clf()
    buffer.seek(0)
    return buffer


def draw_history_chart(name: str):
    logger.debug(F'正在绘制服务器 [{name}] 状态图表……')
    cpu = Globals.cpu_occupation.get(name)
    ram = Globals.ram_occupation.get(name)
    if len(cpu) >= 5:
        pyplot.ylim(0, 100)
        pyplot.xlabel('Time', loc='right')
        pyplot.ylabel('Percentage(%)', loc='top')
        pyplot.title('CPU & RAM Percentage')
        pyplot.plot(cpu, color='red', label='CPU')
        pyplot.plot(ram, color='blue', label='RAM')
        pyplot.legend()
        pyplot.grid()
        buffer = BytesIO()
        pyplot.savefig(buffer, format='png')
        pyplot.clf()
        buffer.seek(0)
        return buffer


async def get_status(server_flag: str = None):
    if server_flag is None:
        if data := await server_manager.get_server_occupation():
            return True, data
        return False, '当前没有已连接的服务器！'
    if server := server_manager.get_server(server_flag):
        if data := await server.send_server_occupation():
            return server.name, data
        return False, F'服务器 [{server_flag}] 未处于监视状态！请重启服务器后再试。'
    return False, F'服务器 [{server_flag}] 未找到！请重启服务器后尝试。'
