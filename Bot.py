import importlib
import subprocess
import sys

def install_global_dependencies():
    # 项目所有第三方依赖：key=导入模块名，value=pip安装包名
    # 下面三个是必要的，不要删！！！
    REQUIRED_DEPS = {
        "mcrcon": "mcrcon",          # MC相关mcrcon依赖
        "uapi": "uapi-sdk-python",   # uapis.cn天气SDK
        "requests": "requests"       # 基础网络请求依赖
    }
    missing_deps = []
    # 检测缺失依赖
    for mod_name, pkg_name in REQUIRED_DEPS.items():
        try:
            importlib.import_module(mod_name)
        except ImportError:
            missing_deps.append(pkg_name)
    # 安装缺失依赖
    if missing_deps:
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", *missing_deps],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except:
            print(f"❌ 依赖安装失败！请手动执行：pip3.12 install {' '.join(missing_deps)}")
            sys.exit(1)

# 启动时立即执行依赖安装
install_global_dependencies()

from atexit import register
from pathlib import Path

import nonebot
from nonebot.adapters.onebot.v11 import Adapter
from nonebot.log import logger

nonebot.init()

nonebot.load_plugins('Plugins')

driver = nonebot.get_driver()
driver.register_adapter(Adapter)


def main():
    log_path = Path('./Logs/')
    if not log_path.exists():
        log_path.mkdir()
    logger.add((log_path / '{time}.log'), rotation='1 day')

    register(shutdown)
    nonebot.run()


@driver.on_startup
async def startup():
    from Scripts import Network
    from Scripts.Servers import Websocket, Http
    from Scripts.Managers import (
        version_manager, data_manager,
        environment_manager, lagrange_manager, resources_manager
    )

    resources_manager.init()

    await version_manager.init()
    await lagrange_manager.init()
    # if version_manager.check_update():
    #     await version_manager.update_version()

    data_manager.load()
    environment_manager.init()
    Websocket.setup_websocket_server()
    Http.setup_api_http_server()
    Http.setup_webui_http_server()

    await Network.send_bot_status(True)


@driver.on_shutdown
async def shutdown():
    from Scripts import Network
    from Scripts.Managers import data_manager

    data_manager.save()

    await Network.send_bot_status(False)


if __name__ == '__main__':
    main()
