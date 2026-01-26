"""
吐槽：原作者，其实除了CPU和RAM，服务器应该还需要读取TPS和MSPT这个重要的数据。
光看前两者占用是看不出毛病的说实话，毕竟直接能反应的就是TPS和MSPT，
我才接触python一个月都不到，个人Python太垃圾不知道怎么搞，
加TPS和MSPT的原理修改：原日志读取→改为RCON直连执行tps/mspt指令，实时返回结果，无日志覆盖问题，
适配Purpur端，RCON方式无日志量限制，稳定性拉满，
目前只适用于Purpur端（或许paper端也能用？），因为我在Purpur端试的，其他端的指令输出可能都不一样，后续做适配？
原来是用的读日志的方法，不过局限性拉满，读不到数据就会出错（这个是当时在湖大幻境社开发群里就有人说了，所有我改了）。
个人捞B，懒得做其他端的适配了，代码和我有一个能跑就行了，搞那么复杂干啥。
新增自动安装依赖：程序启动自动检测mcrcon，未安装则自动下载，纯傻瓜式操作。
20260126 RCON改造+自动装依赖。
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

# ======================读取ServerConfig.json中的MC服务器RCON配置 ======================
def get_mc_rcon_config() -> Dict[str, dict]:
    from pathlib import Path
    config_path = Path(__file__).resolve().parents[3] / "ServerConfig.json"
    try:
        if not config_path.exists():
            logger.critical(f"Status.py：ServerConfig.json不存在，路径：{config_path}")
            return {}
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        mc_rcon = config.get("mc_server_rcon", {})
        if not isinstance(mc_rcon, dict):
            logger.error(f"Status.py：mc_server_rcon格式错误，必须是对象，当前：{type(mc_rcon)}")
            return {}
        # 补全默认配置
        for srv_name, rcon_info in mc_rcon.items():
            rcon_info.setdefault("port", 25575)  # MC默认RCON端口
            rcon_info.setdefault("timeout", 5)   # 默认5秒超时
        logger.success(f"Status.py：成功读取MC RCON配置，共{len(mc_rcon)}台服务器：{list(mc_rcon.keys())}")
        return mc_rcon
    except json.JSONDecodeError:
        logger.critical(f"Status.py：ServerConfig.json不是合法的JSON格式")
        return {}
    except Exception as e:
        logger.critical(f"Status.py：读取ServerConfig.json失败：{str(e)}")
        return {}

MC_RCON_CONFIG = get_mc_rcon_config()

# ======================RCON执行tps/mspt并解析（仅适配Purpur） ======================
def parse_tps_from_rcon(response: str) -> float:
    """
    替换原parse_tps_from_log：解析RCON执行tps指令的返回结果
    适配格式（去掉该死的颜色通配符，Purpur搞什么颜色代码啊，真的是麻烦的死）：TPS from last 5s, 1m, 5m, 15m: 20.0, 20.0, 20.0, 20.0
    返回：5秒内TPS值（第二个数值），失败返回0.0
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
        logger.warning(f"TPS解析失败，过滤颜色符后内容：{clean_resp[:100]}...")
    return tps

def parse_mspt_from_rcon(response: str) -> float:
    """
    替换原parse_mspt_from_log：解析RCON执行mspt指令的返回结果
    适配格式：9.0/7.0/10.9, 9.1/7.0/15.9, 8.6/6.4/20.3
    返回：5秒内MSPT值（第三个段第一个数值），失败返回0.0
    """
    mspt = 0.0
    clean_resp = re.sub(r'§[0-9a-fA-Za-z]|◴', '', response)
    # 正则匹配3个MSPT段，分别对应5s/10s/1m
    mspt_pattern = re.compile(r"(\d+\.\d+/\d+\.\d+/\d+\.\d+),\s*(\d+\.\d+/\d+\.\d+/\d+\.\d+),\s*(\d+\.\d+/\d+\.\d+/\d+\.\d+)")
    mspt_match = mspt_pattern.search(clean_resp)  
    if mspt_match:  
        try:
            avg_mspt = mspt_match.group(1).split('/')[0]  
            mspt = round(float(avg_mspt), 1)
        except (IndexError, ValueError):
            pass
    if mspt == 0.0:
        logger.warning(f"MSPT解析失败，过滤颜色符后内容：{clean_resp[:100]}...")
    return mspt

# 核心修改1：移除内部数据追加，仅返回TPS/MSPT，由主逻辑统一追加
async def get_tps_mspt(server_name: str) -> Tuple[float, float]:
    """    
    保留原函数名/入参/返回值，内部完全替换为RCON逻辑
    适配Purpur：RCON串行执行tps/mspt指令，实时获取结果，无日志写入延迟
    返回：解析后的(TPS, MSPT)，失败均返回0.0
    """
    # 校验服务器是否配置RCON，肯定有腐竹会写错东西，兜底
    if server_name not in MC_RCON_CONFIG:
        logger.warning(f"服务器[{server_name}]未配置RCON信息，无法获取TPS/MSPT")
        return 0.0, 0.0
    rcon_info = MC_RCON_CONFIG[server_name]
    tps, mspt = 0.0, 0.0

    def rcon_operation():
        """同步RCON操作，封装为函数供异步线程调用"""
        try:
            with mcrcon.MCRcon(
                host=rcon_info["host"],
                password=rcon_info["password"],
                port=rcon_info["port"],
                timeout=rcon_info["timeout"]
            ) as rcon:
                tps_resp = rcon.command("tps")   
                mspt_resp = rcon.command("mspt") 
                return parse_tps_from_rcon(tps_resp), parse_mspt_from_rcon(mspt_resp)
        except Exception as e:
            logger.warning(f"服务器[{server_name}]RCON操作失败：{str(e)}")
            return 0.0, 0.0

    try:
        timeout = rcon_info["timeout"] + 1
        tps, mspt = await asyncio.wait_for(
            asyncio.to_thread(rcon_operation),  
            timeout=timeout
        )
        logger.info(f"服务器[{server_name}]RCON获取5秒指标：TPS={tps} | MSPT={mspt}ms")
    except asyncio.TimeoutError:
        logger.warning(f"服务器[{server_name}]RCON操作超时（{timeout}秒），无法获取5秒TPS/MSPT")
    except Exception as e:
        logger.warning(f"服务器[{server_name}]获取5秒TPS/MSPT失败：{str(e)}")
    return tps, mspt 

def init_and_append_history(server_name: str, data_dict: dict, time_dict: dict, value: float, max_len: int, current_time: str):
    """
    统一初始化全局字典+追加数据+截断超长数据
    server_name: 服务器名
    data_dict: 指标全局字典
    time_dict: 时间全局字典
    value: 要追加的指标值
    max_len: 最大历史长度（Globals.MAX_HISTORY_LENGTH）
    current_time: 统一的采集时间戳
    """
    if server_name not in data_dict:
        data_dict[server_name] = []
    if server_name not in time_dict:
        time_dict[server_name] = []
    data_dict[server_name].append(value)
    time_dict[server_name].append(current_time)
    # 截断超长数据（数据和时间同步截断，保证长度一致）
    if len(data_dict[server_name]) > max_len:
        data_dict[server_name].pop(0)
        time_dict[server_name].pop(0)

def choose_font():
    from matplotlib import rcParams  
    # 全局配置matplotlib，强制使用中文字体渲染，关闭负号乱码
    rcParams['font.sans-serif'] = ['SimHei', 'KaiTi', 'Microsoft YaHei', 'DejaVu Sans']
    rcParams['axes.unicode_minus'] = False  # 解决负号显示为方块的问题
    # 关闭字体相关的警告
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
    max_len = Globals.MAX_HISTORY_LENGTH  # 统一最大长度，避免硬写

    if args := args.extract_plain_text().strip():
        flag, response = await get_status(args)
        if flag is False:
            await matcher.finish(response)
        # 调用解析函数获取TPS/MSPT
        tps, mspt = await get_tps_mspt(flag)
        cpu, ram = response
        server_name = flag

        init_and_append_history(server_name, Globals.cpu_occupation, Globals.cpu_time, cpu, max_len, current_time)
        init_and_append_history(server_name, Globals.ram_occupation, Globals.ram_time, ram, max_len, current_time)
        init_and_append_history(server_name, Globals.tps_occupation, Globals.tps_time, tps, max_len, current_time)
        init_and_append_history(server_name, Globals.mspt_occupation, Globals.mspt_time, mspt, max_len, current_time)

        message = turn_message(detailed_handler(flag, response, tps, mspt))
        await matcher.finish(message)
    
    # 批量查询所有服务器
    flag, response = await get_status()
    if flag is False:
        await matcher.finish(response)
    # 批量解析所有服务器TPS/MSPT
    tps_mspt_data = {}
    for server_name in response.keys():
        tps_mspt_data[server_name] = await get_tps_mspt(server_name)
        cpu, ram = response[server_name]
        
        init_and_append_history(server_name, Globals.cpu_occupation, Globals.cpu_time, cpu, max_len, current_time)
        init_and_append_history(server_name, Globals.ram_occupation, Globals.ram_time, ram, max_len, current_time)
        init_and_append_history(server_name, Globals.tps_occupation, Globals.tps_time, tps_mspt_data[server_name][0], max_len, current_time)
        init_and_append_history(server_name, Globals.mspt_occupation, Globals.mspt_time, tps_mspt_data[server_name][1], max_len, current_time)
    
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
                yield F'  TPS（5秒内平均）：{tps:.1f}'
                yield F'  MSPT（5秒内平均）：{mspt:.1f}ms'
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
        yield '\n服务器多次查询趋势折线图：'
        yield str(MessageSegment.image(chart))
    else:  # 图表为None时，不发图片
        yield '\n暂无法绘制趋势图：历史查询次数不足2次，请多次执行【server status】后重试'
    return None

def detailed_handler(name: str, data: list, tps: float, mspt: float):
    cpu, ram = data
    yield F'服务器 [{name}] 的详细信息：'
    yield F'  内存使用率：{ram:.1f}%'
    yield F'  CPU 使用率：{cpu:.1f}%'
    yield F'  TPS（5秒内平均）：{tps:.1f}'
    yield F'  MSPT（5秒内平均）：{mspt:.1f}ms'
    if image := draw_history_chart(name):
        yield '\n服务器的占用历史记录：'
        yield str(MessageSegment.image(image))
        return None
    yield '\n暂无法绘制历史趋势图：后台监控数据不足5次，请稍等片刻重试！'
    return None

# -------------------------绘图区---------------------------------
# 以下是适配TPS与MSPT的图像，从之前的柱状改折线了
def draw_chart(data: dict, tps_mspt_data: dict):
    """
    - 单服务器：同色（蓝色）+ 不同标记区分CPU/RAM/TPS/MSPT
    - 多服务器：每台专属颜色 + 统一标记规则，支持多机对比
    - X轴：采集时间（时:分:秒）
    - Y轴：CPU/RAM 0-100%，TPS/MSPT 0-70
    """
    logger.debug('绘制服务器趋势图：单服同色不同标记+多服专属颜色+时间轴')
    # 过滤有监视数据的服务器
    valid_servers = {name: occ for name, occ in data.items() if occ}
    if not valid_servers:
        return None
    server_names = list(valid_servers.keys())
    server_count = len(valid_servers)

    # 读取各服务器的历史监控数据
    all_history = {}
    for name in server_names:
        cpu_list = Globals.cpu_occupation.get(name, [])
        ram_list = Globals.ram_occupation.get(name, [])
        tps_list = Globals.tps_occupation.get(name, [])
        mspt_list = Globals.mspt_occupation.get(name, [])
        real_time_list = Globals.cpu_time.get(name, [])

        # 统一所有指标和时间的长度
        min_data_len = min(len(cpu_list), len(ram_list), len(tps_list), len(mspt_list), len(real_time_list))
        all_history[name] = {
            "cpu": cpu_list[-min_data_len:] if min_data_len > 0 else [],
            "ram": ram_list[-min_data_len:] if min_data_len > 0 else [],
            "tps": tps_list[-min_data_len:] if min_data_len > 0 else [],
            "mspt": mspt_list[-min_data_len:] if min_data_len > 0 else [],
            "times": real_time_list[-min_data_len:] if min_data_len > 0 else []  # 真实采集时间
        }

    # 过滤没法画点的服务器（2个点才绘制）
    valid_history = {k: v for k, v in all_history.items() if len(v["times"]) >= 2}
    if not valid_history:
        logger.warning(f"监控数据不足2个点，无法绘制折线图")
        return None
    server_names = list(valid_history.keys())
    server_count = len(valid_history)
    # 统一X轴时间（所有服务器取第一台的时间轴，保证对齐）
    base_times = valid_history[server_names[0]]["times"]

    fig, ax1 = plt.subplots(figsize=(12, 6), dpi=120)
    ax2 = ax1.twinx()  # 双Y轴：左轴CPU/RAM，右轴TPS/MSPT

    index_markers = {"CPU": "o", "RAM": "s", "TPS": "^", "MSPT": "p"}  
    single_server_color = "#3182ce"  
    multi_server_colors = ["#3182ce", "#e53e3e", "#38a169", "#ed8936", "#9f7aea"]  
    line_width = 2.5  
    marker_size = 8   

    # ------------------- 单服务器逻辑-------------------
    if server_count == 1:
        server_name = server_names[0]
        s_data = valid_history[server_name]
        # 左Y轴：CPU/RAM（0-100%）
        ax1.plot(base_times, s_data["cpu"], label=f"{server_name}-CPU(%)",
                 color=single_server_color, marker=index_markers["CPU"],
                 linewidth=line_width, markersize=marker_size)
        ax1.plot(base_times, s_data["ram"], label=f"{server_name}-RAM(%)",
                 color=single_server_color, marker=index_markers["RAM"],
                 linewidth=line_width, markersize=marker_size)
        ax1.set_ylim(0, 100)  
        # 右Y轴：TPS/MSPT
        ax2.plot(base_times, s_data["tps"], label=f"{server_name}-TPS(5s)",
                 color=single_server_color, marker=index_markers["TPS"],
                 linewidth=line_width, markersize=marker_size)
        ax2.plot(base_times, s_data["mspt"], label=f"{server_name}-MSPT(5s)",
                 color=single_server_color, marker=index_markers["MSPT"],
                 linewidth=line_width, markersize=marker_size)
        ax2.set_ylim(0, 70)
        # 图表标题
        chart_title = f"{server_name} - 趋势监控（{len(base_times)}次采集）"

    # ------------------- 多服务器逻辑-------------------
    else:
        for idx, server_name in enumerate(server_names):
            s_data = valid_history[server_name]
            # 为每台服务器分配专属颜色
            curr_color = multi_server_colors[idx % len(multi_server_colors)]
            # 左Y轴：CPU/RAM（0-100%）
            ax1.plot(base_times, s_data["cpu"], label=f"{server_name}-CPU(%)",
                     color=curr_color, marker=index_markers["CPU"],
                     linewidth=line_width, markersize=marker_size)
            ax1.plot(base_times, s_data["ram"], label=f"{server_name}-RAM(%)",
                     color=curr_color, marker=index_markers["RAM"],
                     linewidth=line_width, markersize=marker_size)
            # 右Y轴：TPS/MSPT
            ax2.plot(base_times, s_data["tps"], label=f"{server_name}-TPS(5s)",
                     color=curr_color, marker=index_markers["TPS"],
                     linewidth=line_width, markersize=marker_size)
            ax2.plot(base_times, s_data["mspt"], label=f"{server_name}-MSPT(5s)",
                     color=curr_color, marker=index_markers["MSPT"],
                     linewidth=line_width, markersize=marker_size)
        ax1.set_ylim(0, 100)
        ax2.set_ylim(0, 70)
        chart_title = f"多服务器5秒级趋势监控（{server_count}台 · {len(base_times)}次采集）"

    # ------------------- 统一坐标轴/图例配置-------------------
    ax1.set_xlabel('采集时间（时:分:秒）', fontproperties=font, fontsize=12, labelpad=8)
    ax1.set_ylabel('CPU / RAM 使用率 (%)', fontproperties=font, fontsize=12, labelpad=8)
    ax2.set_ylabel('TPS / MSPT (ms)', fontproperties=font, fontsize=12, labelpad=8)
    # X轴时间旋转45度，避免文字重叠
    ax1.tick_params(axis="x", rotation=45, labelsize=10)
    ax1.tick_params(axis="y", labelsize=10)
    ax2.tick_params(axis="y", labelsize=10)
    ax1.grid(True, alpha=0.2, linestyle="-")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right",
               prop=font, framealpha=0.8, ncol=2, fontsize=9)
    # 图表标题
    ax1.set_title(chart_title, fontproperties=font, fontsize=14, pad=15, fontweight="bold")
    # 自动适配布局
    fig.tight_layout()

    # ------------------- 保存图片并释放资源-------------------
    buffer = BytesIO()
    fig.savefig(buffer, format='png', dpi=120, bbox_inches='tight')
    plt.close(fig)  
    buffer.seek(0)
    return buffer  

def draw_history_chart(name: str):
    """
    单服务器历史趋势图：双Y轴折线图（与批量查询样式统一）
    左Y轴：CPU/RAM使用率(%)  右Y轴：5秒TPS/MSPT(ms)
    仅当4项指标历史数据均大于等于5条时绘制，自动对齐数据长度
    """
    logger.debug(f'绘制服务器 [{name}] 5秒级历史状态折线图……')
    # 从全局读取4项指标历史数据
    cpu_list = Globals.cpu_occupation.get(name, [])
    ram_list = Globals.ram_occupation.get(name, [])
    tps_list = Globals.tps_occupation.get(name, [])
    mspt_list = Globals.mspt_occupation.get(name, [])
    
    # 过滤历史数据不足的情况（至少5条才绘制，避免图表无意义）
    min_data_len = min(len(cpu_list), len(ram_list), len(tps_list), len(mspt_list))
    if min_data_len < 5:
        logger.warning(f"服务器[{name}]历史数据不足5条，无法绘制历史趋势图")
        return None
    
    # 保证四个指标的历史长度一致
    cpu, ram, tps, mspt = [
        lst[-min_data_len:] for lst in [cpu_list, ram_list, tps_list, mspt_list]
    ]
    x_axis = list(range(1, min_data_len + 1))  # X轴：监控次数（每执行1次server status记1次）

    # 创建画布
    fig, ax1 = plt.subplots(figsize=(10, 6))
    ax2 = ax1.twinx()  

    style_config = {
        "CPU(%)": {"color": "#e53e3e", "linestyle": "-", "marker": "o", "linewidth": 2, "markersize": 6},
        "RAM(%)": {"color": "#3182ce", "linestyle": "-", "marker": "s", "linewidth": 2, "markersize": 6},
        "TPS(5s)": {"color": "#38a169", "linestyle": "--", "marker": "^", "linewidth": 2, "markersize": 6},
        "MSPT(ms)": {"color": "#d69e2e", "linestyle": ":", "marker": "p", "linewidth": 2, "markersize": 6}
    }

    # 左Y轴：CPU/RAM使用率（0-105%）
    ax1.plot(x_axis, cpu, label="CPU(%)", **style_config["CPU(%)"])
    ax1.plot(x_axis, ram, label="RAM(%)", **style_config["RAM(%)"])
    ax1.set_ylim(0, 105)
    ax1.set_xlabel('监控次数（每执行1次server status记1次）', loc="right", fontproperties=font, fontsize=12)
    ax1.set_ylabel('CPU/RAM 使用率 (%)', fontproperties=font, fontsize=12, color="#2d3748")
    ax1.tick_params(axis="y", labelcolor="#2d3748")
    ax1.grid(True, alpha=0.3, axis="y")  # 仅Y轴网格，更清晰

    # 右Y轴：TPS/MSPT性能指标（0-70）
    ax2.plot(x_axis, tps, label="TPS(5s)", **style_config["TPS(5s)"])
    ax2.plot(x_axis, mspt, label="MSPT(ms)", **style_config["MSPT(ms)"])
    ax2.set_ylim(0, 70)
    ax2.set_ylabel('TPS / MSPT (ms)', fontproperties=font, fontsize=12, color="#2d3748")
    ax2.tick_params(axis="y", labelcolor="#2d3748")

    # 合并双Y轴图例，右上角显示（半透明背景，不遮挡折线）
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right", 
               prop=font, framealpha=0.9, fontsize=10)

    # 图表标题：明确服务器名+5秒级指标+历史监控
    ax1.set_title(f'{name} - 历史状态监控（共{min_data_len}次查询）', 
                  fontproperties=font, fontsize=14, pad=20)

    # 紧凑布局，防止标签/标题被裁剪
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
    return False, F'服务器 [{server_flag}] 未找到！请重启服务器后尝试。'
