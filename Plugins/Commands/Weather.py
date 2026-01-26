import ssl
from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, Message
from nonebot.params import CommandArg

from uapi import UapiClient
from uapi.errors import UapiError

from requests.packages.urllib3.exceptions import InsecureRequestWarning
import requests
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

ssl._create_default_https_context = ssl._create_unverified_context
weather_cmd = on_command("weather", priority=5, block=True)
client = UapiClient("https://uapis.cn")

@weather_cmd.handle()
async def handle_weather(event: MessageEvent, args: Message = CommandArg()):
    city_name = args.extract_plain_text().strip()
    if not city_name:
        await weather_cmd.finish("请输入城市名！示例：.weather 北京")

    try:
        result = client.misc.get_misc_weather(
            city=city_name,
            adcode="",
            extended=False,
            indices=False,
            forecast=True  
        )

        if not result:
            await weather_cmd.finish(f"未查询到「{city_name}」的天气信息，请输入标准省/市/区县名")

        # 解析实时天气字段
        province = result.get("province", city_name)
        city = result.get("city", city_name)
        cur_weather = result.get("weather", "未知天气")
        cur_temp = result.get("temperature", 0)
        wind_dir = result.get("wind_direction", "未知")
        wind_power = result.get("wind_power", "0")
        humidity = result.get("humidity", 0)
        update_time = result.get("report_time", "数据未更新")

        # 解析未来1天预报数据
        forecast_data = result.get("forecast", [])
        tomorrow_weather = "暂无预报数据"
        # 判读是否有预报数据，提取第一条
        if len(forecast_data) >= 1:
            tomorrow = forecast_data[0]
            tomorrow_weather_day = tomorrow.get("weather_day", "未知")
            tomorrow_temp_max = tomorrow.get("temp_max", 0)
            tomorrow_temp_min = tomorrow.get("temp_min", 0)
            # 拼接未来1天预报文案
            tomorrow_weather = f"{tomorrow_weather_day}，{tomorrow_temp_min}℃~{tomorrow_temp_max}℃"

        # 调整回复格式：实时天气 + 未来1天预报
        reply_msg = f"""
【{province} · {city}】天气信息 
实时数据更新：{update_time}
实时天气：{cur_weather} 
实时温度：{cur_temp}℃
相对湿度：{humidity}% 
风向风力：{wind_dir} {wind_power}级
未来1天预报：
{tomorrow_weather}
        """.strip()
        
        await weather_cmd.finish(reply_msg)

    # 捕获官方SDK异常
    except UapiError as exc:
        exc_msg = str(exc)[:50]
        await weather_cmd.finish(f"天气API调用失败：{exc_msg}...\n提示：请输入标准行政区划名")

    except Exception as e:
        e_str = str(e)
        if "FinishedException" in e_str:
            return
        error_detail = e_str[:40]
        await weather_cmd.finish(f"天气查询出错：{error_detail}...\n可检查网络或重试")
