from datetime import datetime

from nonebot import on_notice
from nonebot.adapters.onebot.v11 import GroupDecreaseNoticeEvent, GroupIncreaseNoticeEvent, PokeNotifyEvent
from nonebot.log import logger
from nonebot.matcher import Matcher

from Scripts.Config import config
from Scripts.Managers import data_manager, server_manager
from Scripts.Network import request
from Scripts.Utils import Rules, turn_message

import json
import random
import asyncio  
import os

# ======================戳一戳冷却配置 ======================
POKE_COOLDOWN = 120  # 冷却时间，单位秒（建议10-30秒，可按需改）
last_poke_time = 0  # 记录最后一次触发戳一戳的时间戳
POKE_COOLDOWN_MSG = [
    "别戳啦～我需要休息一下，稍后再试吧～",
    "住手！再戳零件都要掉啦，等会儿再玩～",
    "戳太快啦，Dream都没你手速快，稍等片刻～",
    "别戳惹，让机器人充个能（×××），马上就好～",
    "再戳我可要钻回下界门啦，120秒后再找我玩～",
    "手速拉满了属于是，冷却中ing，稍等～",
    "我的CPU要烧啦，缓一缓再戳～",
    "暂停戳戳！正在加载MC冷知识，稍后解锁～",
    "别戳了别戳了，再戳我就把你送进末地城～",
    "冷却中！快去找点MC方块玩玩，马上就好～"
]

matcher = on_notice(rule=Rules.message_rule, priority=15, block=False)
week_mapping = ('一', '二', '三', '四', '五', '六', '日')


@matcher.handle()
async def watch_decrease(event: GroupDecreaseNoticeEvent):
    logger.info(F'检测到用户 {event.user_id} 离开了群聊！')
    if players := data_manager.remove_player(str(event.user_id)):
        for single_player in players:
            await server_manager.execute(F'{config.whitelist_command} remove {single_player}')
        await matcher.finish(F'用户 {event.user_id} 离开了群聊，自动从白名单中移除 {"、".join(players)} 玩家。')


@matcher.handle()
async def watch_increase(event: GroupIncreaseNoticeEvent):
    await matcher.finish('欢迎加入群聊！请仔细阅读群聊公告，并按照要求进行操作。', at_sender=True)


@matcher.handle()
async def watch_poke(event: PokeNotifyEvent, matcher: Matcher):
    # ====================== 新增：冷却判断（核心防刷屏） ======================
    global last_poke_time
    current_time = event.time  # 获取当前戳一戳的时间戳
    if current_time - last_poke_time < POKE_COOLDOWN:
        cool_msg = random.choice(POKE_COOLDOWN_MSG)  # 随机选一句冷却回应
        await matcher.finish(cool_msg, at_sender=True)
    last_poke_time = current_time  
    if not event.is_tome():
        return None
    
    # ---------------------- 核心改动：用 asyncio 实现异步文件读取 ----------------------
    try:
        def read_local_json():
            """同步读取文件，交给 asyncio 线程池执行"""
            # 拼接绝对路径：BotServer/Config/mc_wiki_database.json
            json_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "Config",
                "mc_wiki_database.json"
            )
            with open(json_path, mode='r', encoding='utf-8') as f:
                return f.read()
        json_content = await asyncio.get_event_loop().run_in_executor(
            None,  
            read_local_json  
        )
        mc_data_list = json.loads(json_content) 
        # 关键校验：确保是列表且有数据，避免随机失效
        if not isinstance(mc_data_list, list) or len(mc_data_list) == 0:
            raise ValueError("JSON 文件必须是数组格式（[]），且至少包含1条数据！")        
        sentence = random.choice(mc_data_list)
    except FileNotFoundError:
        sentence = {"content": "本地 MC 冷知识文件未找到，请检查路径！", "title": "错误", "category": "系统提示"}
    except json.JSONDecodeError:
        sentence = {"content": "本地 mc_wiki_database.json 文件格式错误，请检查 JSON 语法！", "title": "错误", "category": "系统提示"}
    except Exception as e:
        sentence = {"content": f"读取本地数据失败：{str(e)}", "title": "错误", "category": "系统提示"}
    message = turn_message(poke_handler(sentence))
    await matcher.finish(message)


def poke_handler(sentence):
    now = datetime.now()
    yield F'{now.strftime("%Y-%m-%d")} 星期{week_mapping[now.weekday()]}  {now.strftime("%H:%M:%S")}'
    if sentence is not None:
        yield F'\n「{sentence["content"]}」'
#        yield F'               —— {sentence["author"]}《{sentence["origin"]}》'
        yield F' —— {sentence["title"]}《{sentence["category"]}》'
