<<<<<<< HEAD
# hm_launcher



## Getting started

To make it easy for you to get started with GitLab, here's a list of recommended next steps.

Already a pro? Just edit this README.md and make it your own. Want to make it easy? [Use the template at the bottom](#editing-this-readme)!

## Add your files

- [ ] [Create](https://docs.gitlab.com/ee/user/project/repository/web_editor.html#create-a-file) or [upload](https://docs.gitlab.com/ee/user/project/repository/web_editor.html#upload-a-file) files
- [ ] [Add files using the command line](https://docs.gitlab.com/topics/git/add_files/#add-files-to-a-git-repository) or push an existing Git repository with the following command:

```
cd existing_repo
git remote add origin https://git.example.local/012/hm_launcher.git
git branch -M main
git push -uf origin main
```

## Integrate with your tools

- [ ] [Set up project integrations](https://git.example.local/012/hm_launcher/-/settings/integrations)

## Collaborate with your team

- [ ] [Invite team members and collaborators](https://docs.gitlab.com/ee/user/project/members/)
- [ ] [Create a new merge request](https://docs.gitlab.com/ee/user/project/merge_requests/creating_merge_requests.html)
- [ ] [Automatically close issues from merge requests](https://docs.gitlab.com/ee/user/project/issues/managing_issues.html#closing-issues-automatically)
- [ ] [Enable merge request approvals](https://docs.gitlab.com/ee/user/project/merge_requests/approvals/)
- [ ] [Set auto-merge](https://docs.gitlab.com/user/project/merge_requests/auto_merge/)

## Test and Deploy

Use the built-in continuous integration in GitLab.

- [ ] [Get started with GitLab CI/CD](https://docs.gitlab.com/ee/ci/quick_start/)
- [ ] [Analyze your code for known vulnerabilities with Static Application Security Testing (SAST)](https://docs.gitlab.com/ee/user/application_security/sast/)
- [ ] [Deploy to Kubernetes, Amazon EC2, or Amazon ECS using Auto Deploy](https://docs.gitlab.com/ee/topics/autodevops/requirements.html)
- [ ] [Use pull-based deployments for improved Kubernetes management](https://docs.gitlab.com/ee/user/clusters/agent/)
- [ ] [Set up protected environments](https://docs.gitlab.com/ee/ci/environments/protected_environments.html)

***

# Editing this README

When you're ready to make this README your own, just edit this file and use the handy template below (or feel free to structure it however you want - this is just a starting point!). Thanks to [makeareadme.com](https://www.makeareadme.com/) for this template.

## Suggestions for a good README

Every project is different, so consider which of these sections apply to yours. The sections used in the template are suggestions for most open source projects. Also keep in mind that while a README can be too long and detailed, too long is better than too short. If you think your README is too long, consider utilizing another form of documentation rather than cutting out information.

## Name
Choose a self-explaining name for your project.

## Description
Let people know what your project can do specifically. Provide context and add a link to any reference visitors might be unfamiliar with. A list of Features or a Background subsection can also be added here. If there are alternatives to your project, this is a good place to list differentiating factors.

## Badges
On some READMEs, you may see small images that convey metadata, such as whether or not all the tests are passing for the project. You can use Shields to add some to your README. Many services also have instructions for adding a badge.

## Visuals
Depending on what you are making, it can be a good idea to include screenshots or even a video (you'll frequently see GIFs rather than actual videos). Tools like ttygif can help, but check out Asciinema for a more sophisticated method.

## Installation
Within a particular ecosystem, there may be a common way of installing things, such as using Yarn, NuGet, or Homebrew. However, consider the possibility that whoever is reading your README is a novice and would like more guidance. Listing specific steps helps remove ambiguity and gets people to using your project as quickly as possible. If it only runs in a specific context like a particular programming language version or operating system or has dependencies that have to be installed manually, also add a Requirements subsection.

## Usage
Use examples liberally, and show the expected output if you can. It's helpful to have inline the smallest example of usage that you can demonstrate, while providing links to more sophisticated examples if they are too long to reasonably include in the README.

## Support
Tell people where they can go to for help. It can be any combination of an issue tracker, a chat room, an email address, etc.

## Roadmap
If you have ideas for releases in the future, it is a good idea to list them in the README.

## Contributing
State if you are open to contributions and what your requirements are for accepting them.

For people who want to make changes to your project, it's helpful to have some documentation on how to get started. Perhaps there is a script that they should run or some environment variables that they need to set. Make these steps explicit. These instructions could also be useful to your future self.

You can also document commands to lint the code or run tests. These steps help to ensure high code quality and reduce the likelihood that the changes inadvertently break something. Having instructions for running tests is especially helpful if it requires external setup, such as starting a Selenium server for testing in a browser.

## Authors and acknowledgment
Show your appreciation to those who have contributed to the project.

## License
For open source projects, say how it is licensed.

## Project status
If you have run out of energy or time for your project, put a note at the top of the README saying that development has slowed down or stopped completely. Someone may choose to fork your project or volunteer to step in as a maintainer or owner, allowing your project to keep going. You can also make an explicit request for maintainers.
=======
# Python Runtime Agent

这是一个 Python 3.12 的常驻更新代理，实现你给的结构：

```text
Windows Scheduler
        |
        v
 Python Agent
        |
 +------+--------------+
 |      |              |
 v      v              v
Update Runtime      Config
Manager Manager     Manager
 |
 v
VersionManager
```

## 能力

- 从 GitHub 私有仓库的指定 `branch` / `package_dir` 列出文件。
- 按 `package_pattern` 匹配 `runtime-<version>.zip`。
- 从匹配结果里选择最高版本并下载 zip。
- 将版本安装到 `state_dir/versions/<version>`。
- 用 `current.txt` 维护当前版本，切换时使用临时文件替换。
- 保留激活历史 `history.json`，启动失败时自动回滚。
- `RuntimeManager` 负责启动、停止、重启业务 Runtime。
- Runtime 的 stdout/stderr 会写入 `state_dir/runtime-logs/`，启动秒退时异常会带上日志路径。
- `agent.lock` 防止 Windows Scheduler 重复拉起多个代理实例。
- 支持启动 DLL 或 exe，入口必须在 `runtime.command` 里显式配置。
- 也支持直接启动 exe，只需要改 `runtime.command`。

## 目录结构

```text
python_agent/
  run_agent.py
  config.example.json
  agent/
    config.py
    github_client.py
    update_manager.py
    version_manager.py
    runtime_manager.py
    service.py
```

运行后状态目录类似：

```text
C:\ProgramData\RuntimeAgent\
  current.txt
  history.json
  agent.log
  downloads\
  runtime-logs\
  versions\
    1.2.0\
    1.10.0\
```

## GitHub 私有仓库准备

代理不读取 GitHub Release。它会读取配置里的 `github.branch` 和 `github.package_dir`，在该目录里查找 runtime zip 包，例如：

```text
runtime-1.2.0.zip
runtime-1.10.0.zip
```

包名默认用 `runtime-*.zip` 匹配，版本号默认用 `^runtime-(?P<version>.+)\.zip$` 提取。下载后会解压到 `versions/<version>/`，所以 zip 内需要包含可运行的 DLL/exe，或你的 `runtime.command` 要指向解压后实际存在的入口文件。

token 放环境变量，不建议写进配置文件：

```powershell
setx GITHUB_TOKEN "github_pat_xxx"
```

## 配置

复制示例配置：

```powershell
Copy-Item .\python_agent\config.example.json .\python_agent\config.json
```

关键配置：

```json
{
  "github": {
    "owner": "your-org",
    "repo": "your-private-runtime-repo",
    "token_env": "GITHUB_TOKEN",
    "branch": "main",
    "package_dir": "",
    "package_pattern": "runtime-*.zip",
    "version_regex": "^runtime-(?P<version>.+)\\.zip$"
  },
  "runtime": {
    "command": ["{version_dir}\\YourRuntime.exe"],
    "working_dir": "{version_dir}"
  }
}
```

如果 Runtime 是明确的 exe：

```json
"command": ["{version_dir}\\YourRuntime.exe"]
```

如果 Runtime 是明确的 .NET DLL：

```json
"command": ["dotnet", "{version_dir}\\YourRuntime.dll"]
```

更新检测时，代理会选择 branch 目录里版本号最高的 `runtime-<version>.zip`。只要最高版本高于 `current.txt`，就会下载、解压并切换。

## 运行

单次检查：

```powershell
py -3.12 .\python_agent\run_agent.py --config .\python_agent\config.json --once
```

常驻：

```powershell
py -3.12 .\python_agent\run_agent.py --config .\python_agent\config.json
```

## Windows Scheduler

可以创建一个开机启动任务，执行：

```text
Program/script: C:\Windows\py.exe
Arguments: -3.12 C:\path\to\python_agent\run_agent.py --config C:\path\to\python_agent\config.json
Start in: C:\path\to\python_agent
```

如果希望 Scheduler 周期性拉起，而代理只跑一次，把 Arguments 改成：

```text
-3.12 C:\path\to\python_agent\run_agent.py --config C:\path\to\python_agent\config.json --once
```
>>>>>>> a5247b7 (first commit)
