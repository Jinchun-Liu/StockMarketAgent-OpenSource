# ML workspace

Offline ML is isolated from the online application. Nothing in `apps/api` imports this tree.

```text
datasets/       dataset cards, manifests, point-in-time builders
finetuning/     QLoRA preparation, training and evaluation
quantitative/   Qlib features, price models and execution assumptions
text_impact/    structured event aggregation and text models
fusion/         OOF stacking, calibration and quantile intervals
evaluation/     gold sets, leakage tests, metrics and bootstrap
registry/       MLflow promotion, model cards and reproducibility
configs/        reviewed experiment configurations
```

Executable implementations are added phase by phase. Every entrypoint accepts an immutable dataset manifest and emits an MLflow run; ad-hoc local paths are forbidden in production experiments.
