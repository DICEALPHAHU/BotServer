"""
吐槽：原作者，其实除了CPU和RAM，服务器应该还需要读取TPS和MSPT这个重要的数据。
光看前两者占用是看不出毛病的说实话，毕竟直接能反应的就是TPS和MSPT，
我才接触python一个月都不到，个人Python太垃圾不知道怎么搞，
加TPS和MSPT的原理修改：原日志读取→改为RCON直连执行tps指令，实时返回结果，无日志覆盖问题，
适配Purpur端，RCON方式无日志量限制，稳定性拉满，
目前适配Purpur/Folia/Paper/Spigot端，原生格式精准解析，Spigot端无MSPT支持
本人不做tabTps插件的适配，见谅（那个做适配，正则一大堆，我会死）。
本人也不做Bukkit适配，都2026了还有人用这个端跑服务器，敬你是条汉子。
原来是用的读日志的方法，不过局限性拉满，读不到数据就会出错（这个是当时在湖大幻境社开发群里就有人说了，所以我改了）。
个人捞B，懒得做其他端的适配了，代码和我有一个能跑就行了，搞那么复杂干啥。
20260126 RCON改造+自动装依赖，
20260128 补充多端原生格式适配。
20260130 修复version指令读取过早问题，增加重试+结果校验机制
个人习惯保留注释，不然到时候修起来就是天书，自己fork的时候爱删不删，不过修不好与我没有关系。
糊糊敬上。 
"""

from io import BytesIO
from os.path import exists
import sys
import subprocess
import json
import re
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Tuple
import time  # 用于version指令重试间隔

# NoneBot相关核心导入
from nonebot import on_command
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment, Message
from nonebot.params import CommandArg

# 第三方库核心导入
import mcrcon
import matplotlib.pyplot as plt
from matplotlib.font_manager import findSystemFonts, FontProperties

# 项目内部模块导入
from Scripts import Globals
from Scripts.Managers import server_manager
from Scripts.Utils import Rules, turn_message

SERVER_TYPE_NAME = {}
MC_RCON_CONFIG = {}

# ======================读取ServerConfig.json中的MC服务器RCON配置 ======================
def load_rcon_config():
    global MC_RCON_CONFIG
    try:
        from pathlib import Path
        config_path = Path(__file__).resolve().parents[3] / "ServerConfig.json"
        if not config_path.exists():
            logger.critical(f"RCON配置加载失败：ServerConfig.json不存在，路径：{config_path}")
            return False
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        MC_RCON_CONFIG = config.get("mc_server_rcon", {})
        if not MC_RCON_CONFIG:
            logger.warning(f"RCON配置加载警告：mc_server_rcon节点为空，无可用服务器配置")
            return False
        logger.success(f"RCON配置加载成功，共配置{len(MC_RCON_CONFIG)}台服务器")
        return True
    except Exception as e:
        logger.critical(f"RCON配置加载异常：{str(e)}")
        return False


# ======================通过RCON执行version指令，判断端类型======================
def get_mc_server_type(rcon: mcrcon.MCRcon, retry_times: int = 3, retry_interval: float = 1.0) -> str:
    """
    传入已建立的RCON连接对象，执行version指令判断端类型
    优先级：Purpur > Folia > Paper > Spigot
    新增：重试机制+结果校验，解决服务器启动阶段返回checking version的问题
    :param retry_times: 重试次数，默认3次
    :param retry_interval: 每次重试间隔，单位秒，默认1秒
    :return: 端类型字符串，失败返回unknown
    """
    INVALID_VERSION_KEYWORDS = ["checking version", "please wait", "loading version", "version check"]
    for retry in range(retry_times):
        try:
            version_resp = rcon.command("version")
            clean_resp = re.sub(r'§[0-9a-fA-FxX]|§l|§m|§n|§o|§r', '', version_resp).lower()
            clean_resp = clean_resp.strip()
            
            if any(keyword in clean_resp for keyword in INVALID_VERSION_KEYWORDS):
                logger.info(f"第{retry+1}次执行version指令，获取到临时提示：{clean_resp[:50]}...，即将重试")
                if retry < retry_times - 1:  
                    time.sleep(retry_interval)
                continue
            
            if "purpur" in clean_resp:
                return "purpur"
            elif "folia" in clean_resp:
                return "folia"
            elif "paper" in clean_resp:
                return "paper"
            elif "spigot" in clean_resp:
                return "spigot"
            else:
                logger.info(f"未知端类型，version指令过滤后内容：{clean_resp[:200]}...")
                return "unknown"
        except Exception as e:
            logger.warning(f"第{retry+1}次执行version指令失败：{str(e)}")
            if retry < retry_times - 1:
                time.sleep(retry_interval)
            continue

    logger.error(f"执行version指令{retry_times}次均失败，无法判断端类型")
    return "unknown"

def preload_all_server_type():
    global SERVER_TYPE_NAME
    SERVER_TYPE_NAME.clear()
    if not MC_RCON_CONFIG:
        logger.warning("预加载端类型失败：RCON配置未初始化")
        return
    for server_name, rcon_info in MC_RCON_CONFIG.items():
        try:
            with mcrcon.MCRcon(
                host=rcon_info.get("host", "127.0.0.1"),
                password=rcon_info.get("password", ""),
                port=rcon_info.get("port", 25575),
                timeout=rcon_info.get("timeout", 10)
            ) as rcon:
                server_type = get_mc_server_type(rcon, retry_times=3, retry_interval=1.5)
                SERVER_TYPE_NAME[server_name] = server_type
                logger.info(f"服务器[{server_name}]端类型预加载成功：{server_type.capitalize()}")
        except Exception as e:
            SERVER_TYPE_NAME[server_name] = "unknown"
            logger.error(f"服务器[{server_name}]端类型预加载失败：{str(e)}，标记为未知")
    logger.success(f"所有服务器端类型预加载完成，共{len(SERVER_TYPE_NAME)}台 | 未知端类型：{list(SERVER_TYPE_NAME.values()).count('unknown')}台")

if load_rcon_config():
    preload_all_server_type()
else:
    logger.critical("核心初始化失败：RCON配置加载失败，无法预加载端类型，程序可能无法正常运行")

# ===================核心逻辑：获取TPS/MSPT =================
async def get_tps_mspt(server_name: str) -> Tuple[float, float]:
    if server_name not in MC_RCON_CONFIG:
        logger.warning(f"服务器[{server_name}]未配置RCON信息，无法获取TPS/MSPT")
        return 0.0, 0.0
    server_type = SERVER_TYPE_NAME.get(server_name, "unknown")
    if server_type == "unknown":
        logger.warning(f"服务器[{server_name}]端类型为未知，无法执行专属解析")
        return 0.0, 0.0
    
    rcon_info = MC_RCON_CONFIG[server_name]
    tps, mspt = 0.0, 0.0

    def rcon_operation():
        try:
            with mcrcon.MCRcon(
                host=rcon_info["host"],
                password=rcon_info["password"],
                port=rcon_info["port"],
                timeout=rcon_info.get("timeout", 15)  # 修复：把()改成get()，字典不能直接调用
            ) as rcon:
                logger.info(f"服务器[{server_name}]（{server_type.capitalize()}端）开始执行TPS/MSPT解析")
                tps_resp = rcon.command("tps")
                mspt_resp = rcon.command("mspt")
                if server_type == "purpur":
                    t = parse_tps_from_rcon_purpur(tps_resp)
                    m = parse_mspt_from_rcon_purpur(mspt_resp)
                elif server_type == "folia":
                    t = parse_tps_from_rcon_folia(tps_resp)
                    m = parse_mspt_from_rcon_folia(mspt_resp)
                elif server_type == "paper":
                    t = parse_tps_from_rcon_paper(tps_resp)
                    m = parse_mspt_from_rcon_paper(mspt_resp)
                elif server_type == "spigot":
                    t = parse_tps_from_rcon_spigot(tps_resp)
                    m = parse_mspt_from_rcon_spigot(mspt_resp)
                else:
                    t, m = 0.0, 0.0
                return t, m
        except Exception as e:
            logger.warning(f"服务器[{server_name}]RCON执行tps/mspt失败：{str(e)}")
            return 0.0, 0.0

    try:
        timeout = rcon_info.get("timeout", 15)
        tps, mspt = await asyncio.wait_for(
            asyncio.to_thread(rcon_operation),  
            timeout=timeout
        )
        logger.info(f"服务器[{server_name}]TPS/MSPT获取成功：TPS={tps} | MSPT={mspt}ms")
    except asyncio.TimeoutError:
        logger.warning(f"服务器[{server_name}]RCON操作超时（{timeout}秒），无法获取TPS/MSPT")
    except Exception as e:
        logger.warning(f"服务器[{server_name}]获取TPS/MSPT失败：{str(e)}")
    return tps, mspt 


# ======================RCON执行tps/mspt并解析 ======================
# --- Purpur端专属解析---
def parse_tps_from_rcon_purpur(response: str) -> float:
    """
    Purpur端专属：解析RCON执行tps指令的返回结果
    适配格式：TPS from last 5s, 1m, 5m, 15m: 20.0, 20.0, 20.0, 20.0
    返回：5秒内TPS值，失败返回0.0
    """
    tps = 0.0
    clean_resp = re.sub(r'§[0-9a-fA-Za-z]', '', response)
    tps_pattern = re.compile(r"TPS from last 5s, 1m, 5m, 15m:\s*(.+?)$")
    tps_match = tps_pattern.search(clean_resp)  
    if tps_match:  
        num_str_list = [s.strip() for s in tps_match.group(1).split(',') if s.strip()]
        if len(num_str_list) >= 2:
            try:
                tps = round(float(num_str_list[0]), 1)
            except ValueError:
                pass
    if tps == 0.0:
        logger.warning(f"Purpur端TPS解析失败，过滤颜色符后内容：{clean_resp[:100]}...")
    return tps

def parse_mspt_from_rcon_purpur(response: str) -> float:
    """
    Purpur端专属：解析RCON执行mspt指令的返回结果
    适配格式：9.0/7.0/10.9, 9.1/7.0/15.9, 8.6/6.4/20.3
    返回：5秒内MSPT值，失败返回0.0
    """
    mspt = 0.0
    clean_resp = re.sub(r'§[0-9a-fA-Za-z]|◴', '', response)
    mspt_pattern = re.compile(r"(\d+\.\d+/\d+\.\d+/\d+\.\d+),\s*(\d+\.\d+/\d+\.\d+/\d+\.\d+),\s*(\d+\.\d+/\d+\.\d+/\d+\.\d+)")
    mspt_match = mspt_pattern.search(clean_resp)  
    if mspt_match:  
        try:
            avg_mspt = mspt_match.group(1).split('/')[0]  
            mspt = round(float(avg_mspt), 1)
        except (IndexError, ValueError):
            pass
    if mspt == 0.0:
        logger.warning(f"Purpur端MSPT解析失败，过滤颜色符后内容：{clean_resp[:100]}...")
    return mspt

# --- Folia端专属解析---
def parse_tps_from_rcon_folia(response: str) -> float:
    """
    Folia端专属：解析RCON执行tps指令的原生格式
    适配原生格式：§x§4§f§a§4§f§0§lServer Health Report...Lowest/Median/Highest Region TPS: §x§1§e§c§c§5§820.00
    返回：Median Region TPS，失败返回0.0
    吐槽：你麻痹的Folia，一个tps搞得花里胡哨的。
    """
    tps = 0.0
    clean_resp = re.sub(r'§[0-9a-fA-Za-xX]', '', response).replace("  ", " ").strip()
    # Median Region TPS
    tps_pattern = re.compile(r'Median Region TPS:\s*(\d+(\.\d+)?)', re.IGNORECASE)
    tps_match = tps_pattern.search(clean_resp)
    if tps_match:
        try:
            tps = round(float(tps_match.group(1)), 1)
        except ValueError:
            pass
    # 兜底：若中位数匹配失败，尝试匹配最低/最高TPS
    if tps == 0.0:
        fallback_pattern = re.compile(r'(Lowest|Highest) Region TPS:\s*(\d+(\.\d+)?)', re.IGNORECASE)
        fallback_match = fallback_pattern.search(clean_resp)
        if fallback_match:
            try:
                tps = round(float(fallback_match.group(2)), 1)
                logger.info(f"Folia端中位数TPS匹配失败，使用{fallback_match.group(1)} Region TPS替代：{tps}")
            except ValueError:
                pass
    if tps == 0.0:
        logger.warning(f"Folia端TPS解析失败，过滤颜色符后核心内容：{clean_resp[:200]}...")
    return tps

def parse_mspt_from_rcon_folia(response: str) -> float:
    """
    Folia端专属：解析RCON执行mspt指令的原生返回结果
    适配原生格式：§6Server tick times §e(§7avg§e/§7min§e/§7max§e)§6 from last 5s§7,...§6◴ §a0.0§7/§a0.0§7/§a0.0...
    与Paper端MSPT格式完全一致，直接复用Paper端解析逻辑
    返回：5秒内平均MSPT值，失败返回0.0
    """
    return parse_mspt_from_rcon_paper(response)

# --- Paper端专属解析---
def parse_tps_from_rcon_paper(response: str) -> float:
    """
    Paper端专属：解析RCON执行tps指令的原生返回结果
    适配原生格式：§6TPS from last 1m, 5m, 15m: §a20.0, §a*20.0, §a*20.0
    返回：1分钟平均TPS值，失败返回0.0
    """
    tps = 0.0
    clean_resp = re.sub(r'§[0-9a-fA-Za-z]', '', response)
    tps_pattern = re.compile(r"TPS from last 1m, 5m, 15m:\s*(.+?)$")
    tps_match = tps_pattern.search(clean_resp)
    if tps_match:
        num_str_list = [s.strip().replace("*", "") for s in tps_match.group(1).split(',') if s.strip()]
        if num_str_list:
            try:
                tps = round(float(num_str_list[0]), 1)
            except ValueError:
                pass
    if tps == 0.0:
        logger.warning(f"Paper端TPS解析失败，过滤颜色符后内容：{clean_resp[:100]}...")
    return tps

def parse_mspt_from_rcon_paper(response: str) -> float:
    """
    Paper端专属：解析RCON执行mspt指令的原生返回结果
    适配原生格式：§6Server tick times §e(§7avg§e/§7min§e/§7max§e)§6 from last 5s§7,...§6◴ §a0.2§7/§a0.1§7/§a0.3...
    返回：5秒内平均MSPT值，失败返回0.0
    """
    mspt = 0.0
    clean_resp = re.sub(r'§[0-9a-fA-Za-z]|◴', '', response).replace("  ", " ").strip()
    mspt_pattern = re.compile(r'\(avg/min/max\).*from last 5s, 10s, 1m:\s*(.+?)$', re.DOTALL)
    mspt_match = mspt_pattern.search(clean_resp)
    if mspt_match:
        group_str = mspt_match.group(1).strip()
        mspt_groups = [g.strip() for g in group_str.split(',') if g.strip()]
        if mspt_groups:
            five_sec_data = mspt_groups[0].split('/')
            if len(five_sec_data) >= 1:
                try:
                    mspt = round(float(five_sec_data[0].strip()), 1)
                except ValueError:
                    pass
    if mspt == 0.0:
        logger.warning(f"Paper端MSPT解析失败，过滤颜色符后内容：{clean_resp[:200]}...")
    return mspt

# --- Spigot端专属解析---
def parse_tps_from_rcon_spigot(response: str) -> float:
    """
    Spigot端专属：解析RCON执行tps指令的原生返回结果
    适配原生格式：§6TPS from last 1m, 5m, 15m: §a20.0, §a*20.0, §a*20.0
    返回：1分钟平均TPS值，失败返回0.0
    """
    tps = 0.0
    clean_resp = re.sub(r'§[0-9a-fA-Za-z]', '', response)
    tps_pattern = re.compile(r"TPS from last 1m, 5m, 15m:\s*(.+?)$")
    tps_match = tps_pattern.search(clean_resp)
    if tps_match:
        num_str_list = [s.strip().replace("*", "") for s in tps_match.group(1).split(',') if s.strip()]
        if num_str_list:
            try:
                tps = round(float(num_str_list[0]), 1)
            except ValueError:
                pass
    if tps == 0.0:
        logger.warning(f"Spigot端TPS解析失败，过滤颜色符后内容：{clean_resp[:100]}...")
    return tps

def parse_mspt_from_rcon_spigot(response: str) -> float:
    """
    Spigot端专属：解析RCON执行mspt指令的返回结果（兼容ESS插件，实际无MSPT支持）
    适配ESS插件MSPT格式：Average MSPT: 8.5ms
    返回：MSPT值，失败返回0.0并提示
    """
    mspt = 0.0
    clean_resp = re.sub(r'§[0-9a-fA-Za-z]', '', response)
    mspt_pattern = re.compile(r"Average MSPT:\s*(\d+\.\d+)")
    mspt_match = mspt_pattern.search(clean_resp)
    if mspt_match:
        try:
            mspt = round(float(mspt_match.group(1)), 1)
        except ValueError:
            pass
    if mspt == 0.0:
        logger.warning(f"Spigot端MSPT解析失败（tabTps插件未适配），过滤颜色符后内容：{clean_resp[:100]}...")
    return mspt
# ==============================TPS/MSPT适配结束 ===============================

# ======================绘图区======================
def init_and_append_history(server_name: str, data_dict: dict, time_dict: dict, value: float, max_len: int, current_time: str):
    if server_name not in data_dict:
        data_dict[server_name] = []
    if server_name not in time_dict:
        time_dict[server_name] = []
    data_dict[server_name].append(value)
    time_dict[server_name].append(current_time)
    if len(data_dict[server_name]) > max_len:
        data_dict[server_name].pop(0)
        time_dict[server_name].pop(0)

def choose_font():
    from matplotlib import rcParams  
    rcParams['font.sans-serif'] = ['SimHei', 'KaiTi', 'Microsoft YaHei', 'DejaVu Sans']
    rcParams['axes.unicode_minus'] = False  
    rcParams['font.family'] = 'sans-serif'
    import warnings
    warnings.filterwarnings('ignore', category=UserWarning, module='matplotlib')

    for font_format in ('ttf', 'ttc'):
        if exists(f'./Font.{font_format}'):
            logger.info(F'已找到用户设置字体文件，将自动选择该字体作为图表字体。')
            return FontProperties(fname=f'./Font.{font_format}', size=15)
    for font_path in findSystemFonts():
        if 'KAITI' in font_path.upper():
            logger.success(F'自动选择系统字体 {font_path} 设为图表字体。')
            return FontProperties(fname=font_path, size=15)
    logger.warning('未找到楷体和自定义字体，将使用系统备用中文字体绘制图表')
    return FontProperties(size=15)

font = choose_font()
matcher = on_command('server status', force_whitespace=True, block=True, priority=5, rule=Rules.command_rule)

@matcher.handle()
async def handle_group(event: MessageEvent, args: Message = CommandArg()):
    current_time = datetime.now().strftime("%H:%M:%S")
    max_len = Globals.MAX_HISTORY_LENGTH  

    if args := args.extract_plain_text().strip():
        flag, response = await get_status(args)
        if flag is False:
            await matcher.finish(response)
        # 修复1：传args（配置标识）给get_tps_mspt，而非flag（服务器对象/名称）
        tps, mspt = await get_tps_mspt(args)
        cpu, ram = response
        server_name = args

        init_and_append_history(server_name, Globals.cpu_occupation, Globals.cpu_time, cpu, max_len, current_time)
        init_and_append_history(server_name, Globals.ram_occupation, Globals.ram_time, ram, max_len, current_time)
        init_and_append_history(server_name, Globals.tps_occupation, Globals.tps_time, tps, max_len, current_time)
        init_and_append_history(server_name, Globals.mspt_occupation, Globals.mspt_time, mspt, max_len, current_time)

        # 修复2：传args给detailed_handler，匹配字符串参数要求，解决dict not callable
        message = turn_message(detailed_handler(args, response, tps, mspt))
        await matcher.finish(message)
    
    flag, response = await get_status()
    if flag is False:
        await matcher.finish(response)
    tps_mspt_data = {}
    for server_name in response.keys():
        # 修复3：显式解构元组，避免后续取值报错
        tps, mspt = await get_tps_mspt(server_name)
        tps_mspt_data[server_name] = (tps, mspt)
        # 修复4：判断服务器是否在线，避免解构None报错
        if occupation := response[server_name]:
            cpu, ram = occupation
            init_and_append_history(server_name, Globals.cpu_occupation, Globals.cpu_time, cpu, max_len, current_time)
            init_and_append_history(server_name, Globals.ram_occupation, Globals.ram_time, ram, max_len, current_time)
            init_and_append_history(server_name, Globals.tps_occupation, Globals.tps_time, tps, max_len, current_time)
            init_and_append_history(server_name, Globals.mspt_occupation, Globals.mspt_time, mspt, max_len, current_time)
    
    message = turn_message(status_handler(response, tps_mspt_data))
    await matcher.finish(message)

# =========================整合，机器人消息发送区 ====================
def status_handler(data: dict, tps_mspt_data: dict = None):
    yield '已连接的所有服务器信息：'
    for name, occupation in data.items():
        yield F'————— {name} —————'
        if occupation:
            cpu, ram = occupation
            server_type = SERVER_TYPE_NAME.get(name, "未知").capitalize()
            yield F'  端类型：{server_type}'
            yield F'  内存使用率：{ram:.1f}%'
            yield F'  CPU 使用率：{cpu:.1f}%'
            if tps_mspt_data and name in tps_mspt_data:
                tps, mspt = tps_mspt_data[name]
                yield F'  TPS：{tps:.1f}'
                yield F'  MSPT（5秒平均）：{mspt:.1f}ms'
            continue
        yield F'  此服务器未处于监视状态！'
    if font is None:
        yield '\n由于系统中没有找到可用的中文字体，无法显示中文标题。请查看文档自行配置！'
        return None
    if not any(data.values()):
        yield '\n当前没有服务器处于监视状态！无法绘制折线图。'
        return None
    chart = draw_chart(data, tps_mspt_data)
    if chart:  
        yield '\n服务器趋势折线图：'
        yield str(MessageSegment.image(chart))
    else:
        yield '\n无法绘制趋势图：历史监控数据不足2次，请多次执行指令后重试'
    return None

def detailed_handler(name: str, data: list, tps: float, mspt: float):
    cpu, ram = data
    server_type = SERVER_TYPE_NAME.get(name, "未知").capitalize()
    yield F'服务器 [{name}] 的详细监控信息：'
    yield F'  端类型：{server_type}'
    yield F'  内存使用率：{ram:.1f}%'
    yield F'  CPU 使用率：{cpu:.1f}%'
    yield F'  TPS：{tps:.1f}'
    yield F'  MSPT（5秒平均）：{mspt:.1f}ms'
    if image := draw_history_chart(name):
        yield '\n服务器历史监控趋势图：'
        yield str(MessageSegment.image(image))
        return None
    yield '\n无法绘制历史趋势图：监控数据不足5次，请稍等片刻重试！'
    return None

def draw_chart(data: dict, tps_mspt_data: dict):
    logger.debug('绘制多服务器趋势图：CPU/RAM/TPS/MSPT')
    valid_servers = {name: occ for name, occ in data.items() if occ}
    if not valid_servers:
        return None
    server_names = list(valid_servers.keys())
    server_count = len(valid_servers)

    all_history = {}
    for name in server_names:
        cpu_list = Globals.cpu_occupation.get(name, [])
        ram_list = Globals.ram_occupation.get(name, [])
        tps_list = Globals.tps_occupation.get(name, [])
        mspt_list = Globals.mspt_occupation.get(name, [])
        real_time_list = Globals.cpu_time.get(name, [])

        min_data_len = min(len(cpu_list), len(ram_list), len(tps_list), len(mspt_list), len(real_time_list))
        all_history[name] = {
            "cpu": cpu_list[-min_data_len:] if min_data_len > 0 else [],
            "ram": ram_list[-min_data_len:] if min_data_len > 0 else [],
            "tps": tps_list[-min_data_len:] if min_data_len > 0 else [],
            "mspt": mspt_list[-min_data_len:] if min_data_len > 0 else [],
            "times": real_time_list[-min_data_len:] if min_data_len > 0 else []
        }

    valid_history = {k: v for k, v in all_history.items() if len(v["times"]) >= 2}
    if not valid_history:
        logger.warning(f"绘制趋势图失败：所有服务器监控数据均不足2次")
        return None
    server_names = list(valid_history.keys())
    base_times = valid_history[server_names[0]]["times"]

    fig, ax1 = plt.subplots(figsize=(12, 6), dpi=120)
    ax2 = ax1.twinx()  

    index_markers = {"CPU": "o", "RAM": "s", "TPS": "^", "MSPT": "p"}  
    single_server_color = "#3182ce"  
    multi_server_colors = ["#3182ce", "#e53e3e", "#38a169", "#ed8936", "#9f7aea"]  
    line_width = 2.5  
    marker_size = 8   

    if len(valid_history) == 1:
        server_name = server_names[0]
        s_data = valid_history[server_name]
        ax1.plot(base_times, s_data["cpu"], label=f"{server_name}-CPU(%)",
                 color=single_server_color, marker=index_markers["CPU"],
                 linewidth=line_width, markersize=marker_size)
        ax1.plot(base_times, s_data["ram"], label=f"{server_name}-RAM(%)",
                 color=single_server_color, marker=index_markers["RAM"],
                 linewidth=line_width, markersize=marker_size)
        ax1.set_ylim(0, 100)  
        ax2.plot(base_times, s_data["tps"], label=f"{server_name}-TPS",
                 color=single_server_color, marker=index_markers["TPS"],
                 linewidth=line_width, markersize=marker_size)
        ax2.plot(base_times, s_data["mspt"], label=f"{server_name}-MSPT(ms)",
                 color=single_server_color, marker=index_markers["MSPT"],
                 linewidth=line_width, markersize=marker_size)
        ax2.set_ylim(0, 70)
        chart_title = f"{server_name} - 监控趋势（{len(base_times)}次采集）"
    else:
        for idx, server_name in enumerate(server_names):
            s_data = valid_history[server_name]
            curr_color = multi_server_colors[idx % len(multi_server_colors)]
            ax1.plot(base_times, s_data["cpu"], label=f"{server_name}-CPU(%)",
                     color=curr_color, marker=index_markers["CPU"],
                     linewidth=line_width, markersize=marker_size)
            ax1.plot(base_times, s_data["ram"], label=f"{server_name}-RAM(%)",
                     color=curr_color, marker=index_markers["RAM"],
                     linewidth=line_width, markersize=marker_size)
            ax2.plot(base_times, s_data["tps"], label=f"{server_name}-TPS",
                     color=curr_color, marker=index_markers["TPS"],
                     linewidth=line_width, markersize=marker_size)
            ax2.plot(base_times, s_data["mspt"], label=f"{server_name}-MSPT(ms)",
                     color=curr_color, marker=index_markers["MSPT"],
                     linewidth=line_width, markersize=marker_size)
        ax1.set_ylim(0, 100)
        ax2.set_ylim(0, 70)
        chart_title = f"多服务器监控趋势（{len(valid_history)}台 · {len(base_times)}次采集）"

    ax1.set_xlabel('采集时间（时:分:秒）', fontproperties=font, fontsize=12, labelpad=8)
    ax1.set_ylabel('CPU / RAM 使用率 (%)', fontproperties=font, fontsize=12, labelpad=8)
    ax2.set_ylabel('TPS / MSPT (ms)', fontproperties=font, fontsize=12, labelpad=8)
    ax1.tick_params(axis="x", rotation=45, labelsize=10)
    ax1.tick_params(axis="y", labelsize=10)
    ax2.tick_params(axis="y", labelsize=10)
    ax1.grid(True, alpha=0.2, linestyle="-")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right",
               prop=font, framealpha=0.8, ncol=2, fontsize=9)
    ax1.set_title(chart_title, fontproperties=font, fontsize=14, pad=15, fontweight="bold")
    fig.tight_layout()

    buffer = BytesIO()
    fig.savefig(buffer, format='png', dpi=120, bbox_inches='tight')
    plt.close(fig)  
    buffer.seek(0)
    return buffer  

def draw_history_chart(name: str):
    logger.debug(f'绘制服务器 [{name}] 历史趋势图')
    cpu_list = Globals.cpu_occupation.get(name, [])
    ram_list = Globals.ram_occupation.get(name, [])
    tps_list = Globals.tps_occupation.get(name, [])
    mspt_list = Globals.mspt_occupation.get(name, [])
    
    min_data_len = min(len(cpu_list), len(ram_list), len(tps_list), len(mspt_list))
    if min_data_len < 5:
        logger.warning(f"绘制历史趋势图失败：服务器[{name}]监控数据仅{min_data_len}次，不足5次")
        return None
    
    cpu, ram, tps, mspt = [
        lst[-min_data_len:] for lst in [cpu_list, ram_list, tps_list, mspt_list]
    ]
    x_axis = list(range(1, min_data_len + 1))

    fig, ax1 = plt.subplots(figsize=(10, 6))
    ax2 = ax1.twinx()  

    style_config = {
        "CPU(%)": {"color": "#e53e3e", "linestyle": "-", "marker": "o", "linewidth": 2, "markersize": 6},
        "RAM(%)": {"color": "#3182ce", "linestyle": "-", "marker": "s", "linewidth": 2, "markersize": 6},
        "TPS": {"color": "#38a169", "linestyle": "--", "marker": "^", "linewidth": 2, "markersize": 6},
        "MSPT(ms)": {"color": "#d69e2e", "linestyle": ":", "marker": "p", "linewidth": 2, "markersize": 6}
    }

    ax1.plot(x_axis, cpu, label="CPU(%)", **style_config["CPU(%)"])
    ax1.plot(x_axis, ram, label="RAM(%)", **style_config["RAM(%)"])
    ax1.set_ylim(0, 105)
    ax1.set_xlabel('监控次数', loc="right", fontproperties=font, fontsize=12)
    ax1.set_ylabel('CPU/RAM 使用率 (%)', fontproperties=font, fontsize=12, color="#2d3748")
    ax1.tick_params(axis="y", labelcolor="#2d3748")
    ax1.grid(True, alpha=0.3, axis="y")

    ax2.plot(x_axis, tps, label="TPS", **style_config["TPS"])
    ax2.plot(x_axis, mspt, label="MSPT(ms)", **style_config["MSPT(ms)"])
    ax2.set_ylim(0, 70)
    ax2.set_ylabel('TPS / MSPT (ms)', fontproperties=font, fontsize=12, color="#2d3748")
    ax2.tick_params(axis="y", labelcolor="#2d3748")

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right", 
               prop=font, framealpha=0.9, fontsize=10)
    ax1.set_title(f'{name} - 历史监控趋势（共{min_data_len}次查询）', 
                  fontproperties=font, fontsize=14, pad=20)
    fig.tight_layout()

    buffer = BytesIO()
    fig.savefig(buffer, format='png', dpi=100, bbox_inches='tight')
    plt.close(fig)  
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
    return False, F'服务器 [{server_flag}] 未找到！请检查服务器标识是否正确。'
