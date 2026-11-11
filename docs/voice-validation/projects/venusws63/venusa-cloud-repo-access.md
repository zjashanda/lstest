<!-- migrated-from: Polaris/docs/knowledge/venusws63/venusa-cloud-repo-access.md @ a4ef61478f343e599282ba3aac4cc8705fbdae4a -->

# VenusA 云端仓库访问说明

## 仓库信息

- 仓库：`git@cloud.listenai.com:CSKG388976/midea_ac/venusa.git`
- 主验证分支：`mai_ac`
- 本地外部仓库目录约定：`gitBelt/venusa`
- 目录策略：`gitBelt/` 是外部 Git 仓库引用目录，已加入 Polaris 的 `.gitignore`，不要把 venusa 仓库内容嵌入 Polaris 提交。

## 当前可访问账号

当前机器默认 SSH 身份曾认证为 `@listenai_xnzh`，访问该仓库返回 project not found。
显式使用下面私钥时认证为 `@listenai_bszheng`，可以访问 `mai_ac`：

```powershell
C:/Users/Administrator/.ssh/id_ed25519_zjashanda_skills
```

建议在本地 venusa 仓库设置 repo-local SSH 命令，避免走错默认账号：

```powershell
git -C gitBelt/venusa config core.sshCommand "ssh -i C:/Users/Administrator/.ssh/id_ed25519_zjashanda_skills -o IdentitiesOnly=yes -o BatchMode=yes"
git -C gitBelt/venusa fetch origin mai_ac:refs/remotes/origin/mai_ac
```

## 新 PC 使用方式

只同步 Polaris 仓库不会自动带上 `gitBelt/venusa`，新 PC 需要单独准备云端 GitLab 权限：

1. 如果允许复用当前身份：把匹配的私钥 `id_ed25519_zjashanda_skills` 安全放到新 PC 的 `.ssh` 目录，并确保云端已添加对应公钥。
2. 如果不复用私钥：在新 PC 生成新的 SSH key，把新的 `.pub` 公钥添加到云端 GitLab 账号/项目设置。
3. 注意：只有 `.pub` 公钥文件不能完成认证；本机必须持有与云端公钥匹配的私钥。
4. 克隆仓库：

```powershell
New-Item -ItemType Directory -Force gitBelt | Out-Null
git clone -b mai_ac git@cloud.listenai.com:CSKG388976/midea_ac/venusa.git gitBelt/venusa
git -C gitBelt/venusa config core.sshCommand "ssh -i <本机私钥路径> -o IdentitiesOnly=yes -o BatchMode=yes"
```

## 最近确认状态

- `origin/mai_ac` 最新提交：`3d533fe804db031123bf8e12168834808a883137`
- 最新版本提交：`version: 35.03.01.01.18.26.06.04.00.05`
- 本地 Polaris 不跟踪 venusa 源码，只记录访问方式和验证结论。
