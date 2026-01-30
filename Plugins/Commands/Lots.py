"""
抽签小功能，群内专属抽签玩法
触发方式：群里发送「.cq <需要抽签的事宜>」（.为默认命令前缀，可随配置修改）
这我懒得写注释了，基本上这种随机小功能很容易看得懂代码
"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment, Message
from nonebot.params import CommandArg
import random
from datetime import datetime  

# 抽签结果池（你可以随便改）
LOTTERY_RESULTS = ["大吉", "中吉", "小吉", "吉", "末吉", "凶", "大凶"]

def get_today_date():
    now = datetime.now()
    return f"{now.year}年{now.month}月{now.day}日"

lots_cmd = on_command("cq", priority=5, block=True)

@lots_cmd.handle()
async def handle_lots(event: GroupMessageEvent, args: Message = CommandArg()):
    lottery_content = args.extract_plain_text().strip()
    if not lottery_content:
        await lots_cmd.finish(
            MessageSegment.reply(event.message_id) + 
            Message("宝子要输入抽签的事宜哦～\n用法：.cq <你要求签的事情>")
        )
    
    today_date = get_today_date()
    sender_name = event.sender.card or event.sender.nickname or "群友"
    lottery_result = random.choice(LOTTERY_RESULTS)
    
    final_msg = (
        f"今天是{today_date}\n"
        f"{sender_name} 所求事项：【{lottery_content}】\n\n"
        f"结果：【{lottery_result}】"
    )
    await lots_cmd.finish(MessageSegment.reply(event.message_id) + Message(final_msg))