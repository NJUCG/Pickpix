# PickPix 图片挑选与批量裁剪工具

## 简介

PickPix 是一个基于 Python + PySide6 的多方法图片对比、书签管理和批量裁剪工具，适合在多组结果图之间做逐帧对比、统一框选和批量导出。

当前版本支持：

- 多方法结果并排预览
- 本地目录与 SFTP 远程目录输入/输出
- 每个方法独立帧偏移
- 克隆方法与差分方法生成
- 每一帧单独维护裁剪框列表
- 工程保存、另存、导入恢复
- 书签跳转
- 当前帧批量裁剪与全帧批量裁剪

输出统一保存为 PNG。读取时优先按当前输入模板扫描，常见场景为 EXR/PNG 序列。

## 主要能力

### 1. 多方法并排对比

- 左侧预览区可同时显示多个方法的同一帧结果
- 方法列表支持勾选显示、全选、全不选
- 每个方法可以设置帧偏移，便于时序对齐对比

### 2. 派生方法

- 克隆：复制一个现有方法，方便设置不同偏移进行对比
- 差分：从两个源方法生成绝对误差图方法

说明：

- 差分方法会按当前帧实时生成，不依赖预先落盘文件
- 克隆方法和差分方法都会随工程文件保存和恢复

### 3. 按帧裁剪框缓存

- 每一帧都有自己的裁剪框列表
- 切换帧时，不会再清空已经添加过的裁剪框
- 切回某一帧时，会自动恢复该帧之前保存的裁剪框列表
- 工程保存时会把所有帧的裁剪框一起写入工程文件

### 4. 工程文件

支持：

- 保存工程
- 工程另存
- 导入工程

工程文件会保存：

- 输入源列表
- 输出目标
- 当前帧
- 方法显示状态
- 方法偏移
- 克隆/差分方法定义
- 每一帧的裁剪框列表
- 书签
- 预览大小
- 缩放和平移状态

说明：工程文件中的远程输入/输出只保存服务器引用和远程路径，不再保存远程账号密码。服务器信息保存在软件自己的配置文件中。

## 环境要求

- Python 3.10+
- Windows 已验证

依赖见 [requirements.txt](requirements.txt)：

- Pillow
- numpy
- opencv-python
- paramiko
- PyYAML
- PySide6

安装依赖：

```bash
pip install -r requirements.txt
```

如果在中国大陆环境安装较慢，可使用镜像：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 运行方式

源码运行：

```bash
python pickpix.py
```

当前入口为 [pickpix.py](pickpix.py)，会启动 PySide6 图形界面。

## 自动发布

仓库已配置 GitHub Actions 工作流 [release.yml](.github/workflows/release.yml)。

行为如下：

- 当代码 push 到 `main` 或 `master` 时，自动在 Windows 环境构建 `dist/pickpix.exe`
- 构建后会补齐 `dist/config/paths.yaml`
- 工作流会自动更新 GitHub 上 tag 为 `latest` 的 Release
- Release 中会上传以下产物：
  - `pickpix.exe`
  - `paths.yaml`
  - `pickpix-windows.zip`

如果需要，也可以在 GitHub Actions 页面手动触发一次发布。

## 输入数据组织

### 本地输入

支持两种常见组织方式。

方式 A：选择一个总目录，目录下每个子目录视为一个方法。

```text
input_root/
  method_a/
    frame0001.exr
    frame0002.exr
  method_b/
    frame0001.png
    frame0002.png
```

方式 B：直接选择某个方法目录。

```text
method_a/
  frame0001.exr
  frame0002.exr
```

### 远程输入

远程 SFTP 输入与本地规则一致，只是路径位于服务器上。

## 输入文件名模板

输入扫描模板可在界面“设置”中修改。

规则：

- 每行一个模板
- 必须包含 `{number}` 占位符
- `*` 可表示任意文本

示例：

- `frame{number}.exr`
- `frame{number}.png`
- `*.{number}.exr`

## 快速开始

推荐流程：

1. 点击“添加输入文件夹”或“添加远程输入”。
2. 点击“选择输出文件夹”或“选择远程输出”。
3. 在右侧方法列表里选择需要显示的方法。
4. 切换到目标帧。
5. 鼠标左键拖拽绘制裁剪框。
6. 点击“添加到裁剪列表”。
7. 如有需要，继续为当前帧添加多个裁剪框。
8. 切换到其他帧时，重复添加该帧自己的裁剪框。
9. 完成后执行“批量裁剪当前帧”或“批量裁剪所有帧”。
10. 如需中断后继续，可使用“保存工程”或“工程另存”。

## 交互说明

### 方法列表

- 支持勾选显示与隐藏
- 支持为每个方法单独设置偏移
- “克隆”用于复制当前方法
- “移除”会删除当前方法；如果有依赖它的差分方法，会一并删除
- “生成差分”会从两个源方法生成新的差分方法

### 帧导航与视图

- 上一帧 / 下一帧：循环切换
- 上 10 帧 / 下 10 帧：按 10 帧跳转，到首尾时自动停住
- 跳转：输入帧号后定位到指定帧
- 收藏当前帧 / 取消收藏当前帧
- 上一书签 / 下一书签 / 书签下拉跳转
- Ctrl + 鼠标滚轮：缩放
- 右键拖拽：平移
- 重置 1:1：恢复默认缩放和平移
- 顶部“预览大小”滑块：调整所有方法预览的单元尺寸

### 裁剪框操作

- 左键拖拽：绘制矩形框
- Shift + 左键拖拽：绘制正方形框
- 添加到裁剪列表：把当前框加入当前帧的裁剪框列表
- 双击裁剪框列表项：删除单个框
- 清空所有裁剪框：清空当前帧的裁剪框列表
- 手动输入坐标：通过 X、Y、宽度、高度直接生成当前框

### 右侧栏

- 右侧工具栏通过分隔条与左侧预览区分开
- 可以拖动中间分隔条调整左右宽度

## 裁剪与输出规则

### 批量裁剪当前帧

会使用“当前帧”的裁剪框列表，对当前显示方法执行裁剪。

输出内容：

- 当前帧目录下每个方法的裁剪图：`frame帧号/method_x/frame帧号_box1.png`、`frame帧号_box2.png` 等
- 当前帧目录下每个方法的框可视化图：`frame帧号/method_x/frame帧号_boxes_map.png`
- 当前帧目录根部的总览图：`frame帧号/frame帧号_summary.png`

### 批量裁剪所有帧

会遍历所有“有裁剪框的帧”，并按每一帧各自的裁剪框列表处理。

也就是说：

- 帧 A 使用帧 A 的裁剪框
- 帧 B 使用帧 B 的裁剪框
- 没有裁剪框的帧会被自动跳过

输出内容：

- 每个方法每一帧的裁剪图
- 每个方法每一帧的框可视化图

说明：

- 该模式不会生成 summary 拼图
- 差分方法会在每一帧实时生成后再参与裁剪输出

### 输出目录示例

```text
output_root/
  frame0001/
    frame0001_summary.png
    method_a/
      frame0001_box1.png
      frame0001_box2.png
      frame0001_boxes_map.png
    method_b/
      frame0001_box1.png
      frame0001_boxes_map.png
  frame0002/
    method_a/
      frame0002_box1.png
```

## 远程 SFTP 配置

服务器信息会保存在软件目录下的 [config/paths.yaml](config/paths.yaml) 的 `servers` 节点中。你可以直接编辑 YAML，也可以在软件里点击“服务器管理”进行维护。

示例：

```yaml
servers:
  server_1:
    label: Server 191
    host: 1.2.3.4
    port: 22
    username: your_user
    password: your_password
```

说明：

- 可以配置多个服务器预设
- 可以在软件里新增、删除、修改服务器，并测试连接
- 远程输入和远程输出都会从这份服务器列表中选择
- 工程文件只会记录所选服务器的 key 和标签，不会保存密码
- 远程路径必须是绝对路径，且以 `/` 开头

## 打包 exe

已验证的打包命令：

```bash
e:/study/Pickpix/.venv/Scripts/python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name pickpix pickpix.py
```

打包完成后，需要保留配置文件：

```bash
dist/config/paths.yaml
```

如果手动打包，记得把 [config/paths.yaml](config/paths.yaml) 复制到 `dist/config/paths.yaml`。

## 项目结构

```text
pickpix.py
requirements.txt
config/
  paths.yaml
pickpix_app/
  backend/
  frontend/
    qt/
      app.py
      dialogs.py
      flow_layout.py
      preview_canvas.py
```

## 说明

- 旧版 Tkinter 前端代码仍在仓库中保留作参考，但当前运行入口已经切换为 PySide6 前端
- `dist/pickpix.exe` 已可直接启动使用
- 工程文件建议单独管理；虽然远程密码已不再写入工程文件，但服务器配置仍属于本机软件数据
