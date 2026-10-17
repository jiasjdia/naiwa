# naiwa 奶蛙桌宠

一个奶家人的 Windows 桌宠。支持眼神跟随、点击 Q 弹、蓄力回弹、拖动、分身、合体，以及有声捧腹大笑。

![奶蛙预览](assets/laugh/f0001.webp)

## 下载运行

在 [Releases](https://github.com/jiasjdia/naiwa/releases/latest) 下载 `naiwa-v1.0.0-windows-x64.zip`，完整解压后双击 `naiwa.exe`。无需安装 Python。

请将整个文件夹放在可写位置，保留 `_internal`、`assets`、`media` 和配置文件。适用于 Windows 10/11 x64。

## 操作

| 操作 | 效果 |
| --- | --- |
| 移动鼠标 | 待机时眼神轻微跟随 |
| 单击身体 | 点击部位局部 Q 弹 |
| 原地长按后松手 | 约 1.5 秒蓄满，增强回弹 |
| 按住左键移动 | 拎起拖动，放下后回弹 |
| 3 秒内点击身体 10 次 | 完整播放有声大笑 |
| 右键宠物 | 设为本体，打开功能菜单 |
| 菜单“复制分身” | 新增一只 0.4 大小的分身 |
| 拖动两只身体重叠后松手 | 合体，大小相加并封顶 1.2 |
| 菜单“归一” | 分身收回当前本体 |
| Ctrl+Alt+C / Ctrl+Alt+V | 复制 / 在鼠标处粘贴分身 |
| Ctrl+Alt+1 | 全体大笑，播放一份共享音轨 |
| Ctrl+Alt+Q | 退出全部桌宠 |

最多同时 20 只；每只空闲 3～4 分钟会独立静音大笑。重复启动会显示已有桌宠群。
菜单“大小”只调整当前本体，重启保留最后的大小，不恢复分身。
右键菜单内还可打开完整使用说明。

## 素材范围

当前只启用捧腹大笑。仓库与发布包保留：432 帧 `assets/laugh/*.webp`、眼部图层与几何数据、完整笑声音轨。
音轨预先转换为 48 kHz 双声道 PCM WAV，保留完整长度和开头静音；运行时无需外部音频解码器。
原始 PNG 帧、视频、跳舞素材、旧音轨、缓存、开发运行时和调试截图均不发布。

## 从源码运行

需要 Windows 和带 tkinter 的 Python 3.10～3.13。

```powershell
python -m pip install -r requirements.txt
python pet_app.py
```

也可双击 `pet.pyw` 无控制台启动。`pet_settings.json` 保存大小；`media_map.json` 保存音量、起始偏移和音画缓冲补偿。
运行日志位于 `_preview/pet.log`，实例元数据在 `.cache`。它们不会提交到仓库。

## 打包

在 Windows x64 和 Python 3.13 下构建：

```powershell
python -m pip install -r requirements-build.txt
python tools/build_release.py
# 可选：使用自己的 Windows 图标
python tools/build_release.py --icon path/to/icon.ico
```

输出为 `releases/naiwa-v1.0.0-windows-x64.zip` 和 `SHA256SUMS.txt`。
构建脚本按清单收集素材，不递归复制工作目录；压缩包内附文件哈希清单。

## 验证

```powershell
python tools/verify_app.py
python tools/verify_app.py --gui
```

测试使用临时设置和静音音频替身；`--gui` 会短暂显示真实 Tk 窗口。
依赖及参考项目说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
