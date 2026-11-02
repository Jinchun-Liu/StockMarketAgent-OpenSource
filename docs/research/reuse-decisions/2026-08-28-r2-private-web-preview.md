# R2 私有 Web 预览复用决策

## 结论

采用仓库现有 Next.js 16、React 19、FastAPI、Pydantic、SQLAlchemy/PostgreSQL、Celery/Valkey、boto3 和
CloudFormation。新增并精确锁定 `oidc-client-ts==3.5.0` 与 `PyJWT==2.13.0`；界面动画使用浏览器原生
CSS，不引入第二套组件或动画框架。

## 采用

- `oidc-client-ts 3.5.0`（Apache-2.0）：浏览器 Authorization Code + PKCE、access-token 生命周期与
  callback 恢复。它不支持隐式流，符合公开客户端不保存 client secret 的边界。
- `PyJWT 2.13.0`（MIT）：只允许 RS256，并同时校验 issuer、client ID、`token_use`、时间和 JWKS。
  2.13.0 包含算法混淆、JWK allow-list 和非 HTTP(S) JWKS URI 等安全修复。
- 已有 `boto3==1.43.51`：在 `ObjectStorePort` 后实现 task-role S3 adapter；不新增 AWS SDK。
- IBM Plex Sans、IBM Plex Mono（OFL-1.1）与 Noto Sans SC（OFL-1.1）：以仓库内固定字体文件自托管，
  构建和运行时不访问字体 CDN。

## 参考但不复制

TIKR 的搜索优先与 Bull/Bear、AlphaSense/Quartr 的来源下钻、Koyfin 的单页扫读、Simply Wall St 的
渐进展开以及 Apple HIG 的可中断短动效与 reduced-motion 原则只作为交互参考。不得复制界面、截图、
品牌资产、文案、数据或产品结构。

## 自建缺口

- v1 兼容且 v2 强认证的双 API 边界；
- principal hash 所有权、不可枚举 404 和会话配额；
- 强类型 runtime config 与 CSP 精确 Cognito 域；
- 完全不复用旧 UI 的“安静研究仪器”和证据透镜；
- 绑定候选 SHA 的私有 CloudFront/ECS/RDS/Valkey/S3 合成预览发布器。

## 供应链与权利

两项新依赖均进入锁文件、依赖注册和离线构建；字体只分发 OFL 许可文件与固定二进制。Cognito、AWS
资源和真实身份不进入测试 fixture 或 Git。外部产品仅为 reference，不存在代码或内容复用。
