# 更新与推送

仓库：[SSXXXzjx/NeonTag](https://github.com/SSXXXzjx/NeonTag)

## 在这台电脑修改后推送

在 `NeonTag` 文件夹打开终端，确认目录里能看到 `main.py`：

```bash
git status
git diff
git add .
git commit -m "说明本次更新了什么"
git push origin main
```

`git status` 查看变更，`git diff` 查看已跟踪文件的具体修改；`git add .` 暂存当前目录内的变更（包括新增、修改和删除），`git commit` 保存为本地版本，`git push` 上传 GitHub。提交说明换成当次实际内容，例如 `更新任务列表布局`。

只提交个别文件时，可将 `git add .` 改成 `git add README.md static/app.js`。没有修改时无需再次提交。

## 在另一台电脑开发

第一次先克隆，再安装依赖：

```bash
git clone https://github.com/SSXXXzjx/NeonTag.git
cd NeonTag
```

如果多台电脑都会改代码，每次开始修改前先同步：

```bash
git pull --ff-only origin main
```

如果推送提示远端有新提交，先用 `git status` 检查并提交本地修改，然后：

```bash
git pull --rebase origin main
git push origin main
```

遇到冲突时按提示编辑冲突文件，执行 `git add <文件>` 和 `git rebase --continue`；不确定如何处理，可用 `git rebase --abort` 回到开始同步之前，再检查差异。不要强制推送覆盖远端历史。

## Linux 服务器更新运行版本

暂停任务并停止服务，进入服务器的项目目录：

```bash
git pull --ff-only origin main
source .venv/bin/activate
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
python main.py
```

服务器代码没有本地修改时可以直接更新。`models/` 和 `.state/` 被忽略，正常更新代码不会删除模型、任务记录或备份。

## 登录与忽略文件

HTTPS 推送需要登录 GitHub。Windows 安装 Git Credential Manager 后，可使用设备登录：

```bash
git credential-manager github login --username SSXXXzjx --device
```

按终端提示打开 GitHub 的设备登录页面并授权。不要将令牌写入仓库地址、代码或 README。

项目 `.gitignore` 已排除模型权重、任务数据、虚拟环境、Python 缓存、日志及 `.env` 配置。输出数据请放在项目目录之外；提交前用 `git status` 确认暂存区只有准备发布的文件。
