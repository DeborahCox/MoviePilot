# Fork 应用内更新

Fork 主程序更新使用 `DeborahCox/MoviePilot` 的 Release：后端从发布标签下载源码归档，
前端从同一 Release 下载 `dist.zip`。站点资源更新仍使用原有资源来源。

发布前在后端 `v3` 分支将 `version.py` 的 `APP_VERSION` 和 `FRONTEND_VERSION`
同时设为新的 `v3.x.y-N`，并确保它高于已运行版本及已有 Fork Release。提交并推送后，
在 GitHub Actions 的 `Sync upstream and build image` 中手动运行，填写 `release_version`。
工作流使用当时的后端和前端 Fork `v3` 提交构建，将前端文件及版本标记打包为
`dist.zip`，在后端仓库创建同名标签和 Release，校验资产摘要后发布。
上游 `build-v3.yml` 发布流程仅在上游仓库执行，避免 Fork 修改版本时触发另一套发布流程。

正式 Release 和标签不可重写；同版本代码变更需递增数字后缀。失败留下的同提交标签或
草稿可以重新运行恢复，已正式发布的版本不可覆盖。标签创建后若 `v3` 已改变，须先排查
草稿并使用新的版本，不要移动已创建的标签。

此流程与定时构建镜像独立，不会自动更新容器镜像或改写既有运行中的实例。应用内更新前，
运行实例必须已经包含指向 Fork 的更新器；旧镜像仍会使用旧来源，应先部署包含该改动的
Fork 镜像。应用内更新只替换程序载荷，不替换 Docker 系统组件；系统基线不满足时仍需更新镜像。
