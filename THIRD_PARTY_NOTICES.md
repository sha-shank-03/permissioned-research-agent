# Runtime and research attribution

The source has no third-party pip dependencies and vendors no runtime binary, model weights, code library or external dataset.

| Component | Use | License/reference |
| --- | --- | --- |
| Python 3.11 standard library | Application/runtime and unittest | [PSF license and notices](https://docs.python.org/3.11/license.html) |
| SQLite bundled with Python | Fictional corpus and metadata | [Public domain dedication](https://www.sqlite.org/copyright.html) |

The [Python sqlite3 documentation](https://docs.python.org/3.11/library/sqlite3.html) and [SQLite transaction documentation](https://www.sqlite.org/lang_transaction.html) informed the local data/authorization boundary. These are references, not copied implementation.

The local single-active-attempt implementation uses standard-library [Windows msvcrt locking](https://docs.python.org/3.11/library/msvcrt.html) or [POSIX fcntl locking](https://docs.python.org/3.11/library/fcntl.html). Those primary documents were checked on 8 October 2026. No third-party locking package is required; local Windows cross-process checks passed in M2, while POSIX execution remains untested.

Glean's [Deep Research documentation](https://docs.glean.com/administration/assistant/configuration/deep-research) and [Context Platform role](https://job-boards.greenhouse.io/gleanwork/jobs/4696752005) are workflow and employer-capability research. This project is independent and makes no claim of their endorsement or integration.

[Zanzibar](https://research.google/pubs/zanzibar-googles-consistent-global-authorization-system/) is conceptual prior art for ordering authorization/content changes. [ALCE](https://aclanthology.org/2023.emnlp-main.398/) motivates distinguishing correctness and citation quality. No code/data from either publication is reused; this repository is not an implementation of either system.

Research references were retrieved on 8 October 2026. The optional wire adapter follows Ollama's [chat API](https://docs.ollama.com/api/chat), [tool calling documentation](https://docs.ollama.com/capabilities/tool-calling) and [v0.40.1 reference](https://github.com/ollama/ollama/tree/v0.40.1). No Ollama executable/code is installed or redistributed; Ollama's reference implementation is [MIT licensed](https://github.com/ollama/ollama/blob/v0.40.1/LICENSE). The project HTTP double uses Python's standard library and executes no inference. Wire compatibility with an actual Ollama server remains untested. Model weights are not redistributed.

The unexecuted standard-runner CI workflow references [actions/checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1) at commit `3d3c42e5aac5ba805825da76410c181273ba90b1` and [actions/setup-python v7.0.0](https://github.com/actions/setup-python/releases/tag/v7.0.0) at commit `5fda3b95a4ea91299a34e894583c3862153e4b97`. Official release commit links were checked on 8 October 2026. Those action repositories are [MIT licensed](https://github.com/actions/checkout/blob/v7.0.1/LICENSE); setup-python's [MIT license](https://github.com/actions/setup-python/blob/v7.0.0/LICENSE) is separate. Their code is fetched by GitHub Actions rather than vendored here. Python is pinned to `3.11.9` following the official [exact-version guidance](https://github.com/actions/setup-python/blob/v7.0.0/docs/advanced-usage.md). The official [runtime manifest](https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json) lists 3.11.9 x64 distributions for Linux 24.04 and Windows, and [runner images](https://github.com/actions/runner-images#available-images) lists the selected Ubuntu/Windows labels. Both were checked on 8 October 2026. There are no pip dependencies, package-lock files, paid model APIs or model weights to provision. Hosted Python setup can download the exact runtime if absent from its tool cache. No action/runtime download or hosted CI execution occurred during this local source slice.
