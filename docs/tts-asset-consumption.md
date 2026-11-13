# TTS 资产消费边界（指向 tts-library.md）

- 更新时间：2026-11-12 17:08:26
- 关联：NOAH-66、`tts-runtime/docs/library-integration.md`

本文件保留为**入口别名**，完整内容见 [`docs/tts-library.md`](tts-library.md)。

lstest 在 TTS 资产消费上的唯一立场：

- `lstest` 只消费**任务目录副本**，不直接读写 1006 共享库。
- 先 `asset-tools tts-library lookup`，命中再 `fetch`；未命中交给 `tts-runtime` 合成。
- 不在 lstest 内建缓存、不计算 cache key、不保存共享凭据。
- 库不可达（退出码 3）不等于资产不存在，禁止据此判定 miss。
