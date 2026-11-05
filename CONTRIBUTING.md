# Contributing

Thanks for helping. Bug reports, fixes and pull requests are all welcome.

## Setup

You need [uv](https://docs.astral.sh/uv/). It installs the right Python on its own.

```bash
uv sync
```

```bash
uv run task dev
```

`task dev` opens the app straight from the source, which is the quickest way to see a change. The commands live in `pyproject.toml` under `[tool.taskipy.tasks]`; the README lists them all.

## Workflow

1. Open an issue first for anything bigger than a small fix, so we can agree on the approach.
2. Write the failing test first, then the change. Tests live in `tests/`, and anything that touches the engine's routes needs one, since they guard who can reach it.
3. Keep the panel one self-contained `ui.html` with no build step and no CDN: fonts ship in `assets/fonts`, and every value from the engine goes into the page through `textContent`, never `innerHTML`.
4. Run the checks before opening a pull request:

```bash
uv run task format
```

```bash
uv run task lint && uv run task test
```

CI runs the same `lint` and `test` jobs on every pull request, and `main` only takes squash merges once both pass.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): subject`, for example `fix(engine): reject requests without the token`.
