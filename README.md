# RoomCraft：住宅三维制作工作流

将户型、空间约束和家具参考转换为可编辑三维场景，输出原生效果图、AI 增强图、全景和本地 3D 漫游。目录包含原项目的调度器、模型调用脚本、Kimi 编写的历史场景工作流和实时漫游网页代码。

**项目约束：所有 Blender 操作和 Blender 脚本编写、修改、验证均须通过 Codex CLI + Kimi K3.1 执行。** 不要直接运行 Blender 脚本；先确认私有配置确实指向所需模型。任务记录中的模型名称只是标签，不是独立的模型身份验证。

## 目录

- `scripts/spatial/`：Codex CLI 调度、Seed3D 家具和 Seedream 参考图增强入口。
- `templates/standard/workflow.py`：历史案例中由 Kimi 编写的 Blender 工作流，支持检查、准备、照明预览、渲染、导出和验证。
- `prepare.py`：从本地私有案例包建立新的运行目录，不调用模型或 Blender。
- `web/`：Three.js 自由漫游页面和交互代码；需自行提供私有模型、导航与灯光文件。
- `task-example.txt`：为新场景准备 Kimi 任务的通用模板。

该历史 Blender 脚本依赖特定场景中的对象名、14 组家具、两组门、2.75 米净高及 9 个全景点位，不是任意户型的一键转换器；其他户型需要通过 Kimi + Blender 改写并人工验收。历史标准场景与后续两卫修正是不同版本，不要混用。

## 开始前准备

需要 Python 3.11+、Node.js、Codex CLI、Blender 以及各模型的可用权限。历史渲染记录的 Blender 版本为 5.2.0 LTS；当前目录的跨机器完整重跑尚未验证。模型调用使用 Ark OpenAI 兼容地址 `https://ark.cn-beijing.volces.com/api/v3`，在本机私有环境配置模型和账号，示例变量见 `.env.example`。不要把实际值写进仓库。

将授权使用的户型图、标注尺寸、净高、风格与家具参考、可编辑场景和材质放在本地。已有私有复现包需解压后提供给 `--bundle`，应包含：

```text
inputs/base-scene.blend
templates/standard/config.json
templates/standard/material-sources/  # 配置中引用的图像
```

这些包含实际案例信息的文件、生成物、日志、密钥和端点不会随仓库发布。`.gitignore` 排除了 `inputs/`、`runs/`、`web/cases/` 和相关凭据。不要强行添加这些目录。使用前确认每份资料的共享权限。

## 标准化流程

从本目录执行；把路径换成自己的解压目录：

```bash
python3 prepare.py --bundle /path/to/private-bundle --name demo01
python3 scripts/spatial/run_roomcraft_workflow.py --run-dir runs/demo01 --stage inspect --dry-run
```

以上只准备文件和任务。dry-run 不验证模型权限、不启动 Blender，也不证明完整工作流可运行。确认模型和私有环境后，按 `inspect`、`prepare`、`lighting-previews`、`render`、`export`、`verify` 依次运行同一调度命令，去掉 `--dry-run`，并将 `--stage` 替换成当前阶段。每步检查实际结果后再运行下一步。

```bash
source "${CODEX_LLM_ENV_FILE:-$HOME/.codex/llm/ark.env}"
python3 scripts/spatial/run_roomcraft_workflow.py --run-dir runs/demo01 --stage inspect
```

如使用其他私有环境文件，通过 `CODEX_LLM_ENV_FILE` 指定。Kimi 调度器支持 `ARK_KIMI_MODEL` 和 `ARK_KIMI_API_KEY`，缺失时读取 `LLM_INVENTORY_FILE` 指向的私有清单。Seed3D 既支持变量别名也会读取私有清单；Seedream 脚本根据清单中 **Seedream 5.0 Pro** 条目选择模型，不能只设置通用默认图像模型。新环境需自行确认清单格式和模型权限。不能将密钥、私有端点、个人路径或原始场景写入任务日志、文档与代码。

执行证据写入 `runs/demo01/jobs/`，场景输入、配置和输出有摘要记录。原工作流使用摘要检查复用缓存；自动检查通过后仍需人工检查布局、机位、材质和可通行性。请勿拿历史检查结果冒充本轮结果。

## AI 家具和二维图片增强

以下从本目录运行，并事先加载自己的私有环境：

```bash
python3 scripts/spatial/seed3d_furniture.py --image /path/to/reference.png --output runs/sofa --wait 0
python3 scripts/spatial/seedream_room_enhance.py --input /path/to/render.png --prompt /path/to/prompt.txt --output runs/master-v1.png --check-config
```

Seed3D 的同一输出目录用于查询已有任务，成功后得到 `asset.glb`；重新生成使用新目录。Seedream 去掉 `--check-config` 才会实际调用模型，并要求新输出名。参考图和提示词、模型标签、输入输出摘要应一起留存。生成可能收费；模型服务、版本和生成结果可能发生变化。

Seed3D 输出需检查尺度、朝向、背面及落地，不能认定为品牌原厂模型。Seedream 图片是二维增强，不会自动更新三维几何、全景、灯光或碰撞；本案例保存的深度/法线/蒙版没有用于本次增强的硬性几何控制。

## 本地 3D 网页

安装前端依赖并从本目录创建本地静态资源链接：

```bash
npm install
mkdir -p web/vendor
ln -s ../../node_modules/three web/vendor/three
```

从私有案例包的 `web/` 中复制最新场景的 `model.glb`、`lighting.json`、`navigation.json` 到 `web/cases/roomcraft-realtime/`；复制历史场景的 `model.glb`、`lighting.json` 到 `web/cases/roomcraft-baseline/`，并复制 `walk/reference-navigation.json` 到 `web/walk/`。两个版本用于网页内的对照切换，目录已被 Git 忽略。

```bash
python3 -m http.server 53200 --bind 127.0.0.1 --directory web
```

打开 http://127.0.0.1:53200/walk.html。页面需要支持 WebGL2；`127.0.0.1` 只能从运行服务的电脑访问。网页是实时近似渲染，与 Cycles 的光线和 AI 静帧不同。旧全景及旧增强图不会随模型自动更新。

## 分享与安全

提交前只检查并暂存要分享的文件。不要提交户型图、品牌照片、个人住址、未经许可的模型、私有案例配置、API 密钥、访问令牌、模型私有端点、日志、浏览器档案或包含这些信息的 Git 历史。用户自备私有素材时也应检查 `.blend` 内嵌数据及生成文件。流程适合方案交流，不替代量房、结构审核或施工图。

## 合成户型：图片到可漫游三维的最小完整链路

新的 `scripts/spatial/run_floorplan_demo.py` 使用公开合成户型验证两种能力：从图片提取可编辑结构，以及将自然语言转换为场景编辑。此演示独立于历史住宅案例，不依赖其私有 Blender 底模。

实际连接方式：`floorplan-editor trace` 输出原始墙线 → 本地适配器用原图整理连续墙段、门窗开口和柱子 → 按明确标定转换成米 → 对照原图复核门窗 → Codex CLI 配置的 Kimi K3.1 生成编辑操作 JSON → 几何检查 → 同一要求的 Kimi CLI 构建、验证、渲染并导出 Blender/GLB → Three.js 查看。

外部校对器为 [armedad/floorplan-editor](https://github.com/armedad/floorplan-editor)，GPL-3.0-only，固定版本 `0c833f84507904cb0ab7587980a7a71a902a26d0`；通过本地独立进程调用，没有把其源代码复制进项目。它的 `trace` 和网页校对器实际参与此链路。不是已经跑通 CubiCasa5K、FloorplanToBlender3d 或 Aedifex 的声明。

安装 Python 3.12+ 与独立依赖：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-demo.lock.txt
npm ci --ignore-scripts
.venv/bin/python scripts/spatial/run_floorplan_demo.py --stage prepare
.venv/bin/python scripts/spatial/run_floorplan_demo.py --stage recognize
```

`prepare` 只下载固定版本的合成样例与外部校对器至忽略目录。`recognize` 的识别输入只有 PNG 和标定信息；配套参考 JSON 只在最后评价时读取。样例比例由其附带的 2 px/inch 信息提供，不是假装从无尺寸图纸中推断出的真实尺寸。净高 2.75m 为演示假设。空间名称、近似材质区域与门窗类别经过人工指定；目前识别器适用于正交、粗黑实墙线，不是通用户型理解模型。

在本地打开校对器，可调整墙线和门窗：

```bash
PYTHONPATH=runs/open-source-demo/vendor/floorplan-editor .venv/bin/python -m floorplan_editor serve runs/open-source-demo/editor --port 53212 --no-browser
```

网页保存后，通过适配器将墙体、门窗改动导回米制场景，然后重新检查、运行后续阶段；当前不导入该编辑器的家具、柜台或文字标签，也不会自动刷新三维：

```bash
.venv/bin/python scripts/spatial/floorplan_pipeline.py import-editor --input runs/open-source-demo/editor/plan.json --config runs/open-source-demo/reviewed.json --output runs/open-source-demo/reimported.json
```

人工复核 `reimported.json` 后替换本轮 `reviewed.json`。`request.txt` 是自然语言输入，`edit.json` 是允许执行的结构化动作；当前支持添加和修改家具，保留墙体不变。家具字段使用唯一 ID、类型、中心点、尺寸、角度、颜色和材质。初步检查包含重复 ID、正尺寸、开口在墙上、家具越界和家具穿墙；额外碰撞与通行检查须查看本轮实际报告，不能仅凭基础验证声称通行安全。

```bash
source "${CODEX_LLM_ENV_FILE:-$HOME/.codex/llm/ark.env}"
.venv/bin/python scripts/spatial/run_floorplan_demo.py --stage edit
.venv/bin/python scripts/spatial/run_floorplan_demo.py --stage build
.venv/bin/python scripts/spatial/run_floorplan_demo.py --stage package
.venv/bin/python -m http.server 53210 --bind 127.0.0.1 --directory web
```

`build` 通过 Codex CLI + Kimi 调用其编写的 Blender 脚本，不在父调度器中直接执行 Blender。改用其他机器时，通过 `--blender` 指定本机可执行文件。每次 CLI 调用保存配置摘要与脱敏事件；配置摘要只能证明选择的配置，不能独立认证供应商后端模型身份。

打开 `http://127.0.0.1:53210/demo/`。页面包含原图/识别叠加、编辑指令和操作、空房/修改后 GLB、俯瞰/自由行走、原生渲染和产物下载。网页只展示已执行的文字指令，新指令需再跑 CLI。初次准备和识别之后，可用 `--stage all` 重跑全部阶段；加 `--reuse-operations` 复用已有 `edit.json`，减少语言模型重新规划导致的差异，Blender 操作仍通过 Kimi CLI。语言模型输出和跨版本渲染不保证逐字节一致。

案例生成后，后续可用 `bash start-demo.sh` 同时启动两个本地服务；按 Ctrl+C 关闭。启动前应先关闭占用 53210/53212 端口的旧服务。该脚本只启动网页，不调用模型或 Blender。

所有输入、外部源码、日志、场景、图片和网页数据留在 `inputs/`、`runs/`、`web/demo/data/` 等忽略目录。网页打包脚本采用白名单，仅复制展示所需文件，不复制密钥或 CLI 原始日志。参数化家具用于流程验证，不代表原厂品牌资产；本轮不包含 Seed3D 家具或 Seedream 增强。

`run-report.json` 汇总产物与脚本摘要、模型配置核对、外部编辑器回传验证、布局检查和 Blender GLB 重新导入结果。浏览器实测记录在本轮 `browser-validation.json`。`check_layout.py` 的默认代表性通路仅适用于此合成户型，其他房屋须用 `--routes` 提供自己的通路，不应将其直接当作通用寻路算法。
