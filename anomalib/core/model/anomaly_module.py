"""Base Anomaly Module for Training Task."""

# Copyright (C) 2020 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions
# and limitations under the License.
import abc
from typing import List, Union

import pytorch_lightning as pl
import torch
from pytorch_lightning.callbacks.base import Callback
from torch import nn

from anomalib.core.results import ClassificationResults, SegmentationResults
from anomalib.utils.metrics import compute_threshold_and_f1_score


class AnomalyModule(pl.LightningModule, abc.ABC):
    """AnomalyModule to train, validate, predict and test images."""

    def __init__(self, task: str, adaptive_threshold: bool, default_threshold: float):
        """BaseAnomalyModule.

        Args:
            task (str): Task type could be either ``classification`` or ``segmentation``
            adaptive_threshold (bool): Boolean to check if threshold is adaptively computed.
            default_threshold (float): Default threshold value.
        """
        # TODO: Address threshold parameters in the next PR.

        super().__init__()
        self.save_hyperparameters()
        self.loss: torch.Tensor
        self.callbacks: List[Callback]
        self.adaptive_threshold = adaptive_threshold
        self.register_buffer("threshold", torch.Tensor([default_threshold]))
        self.threshold: torch.Tensor

        self.model: nn.Module

        self.results: Union[ClassificationResults, SegmentationResults]
        if task == "classification":
            self.results = ClassificationResults()
        elif task == "segmentation":
            self.results = SegmentationResults()
        else:
            raise NotImplementedError("Only Classification and Segmentation tasks are supported in this version.")

    def forward(self, batch):  # pylint: disable=arguments-differ
        """Forward-pass input tensor to the module.

        Args:
            batch (Tensor): Input Tensor

        Returns:
            [Tensor]: Output tensor from the model.
        """
        return self.model(batch)

    def predict_step(self, batch, batch_idx, _):  # pylint: disable=arguments-differ, signature-differs
        """Step function called during :meth:`~pytorch_lightning.trainer.trainer.Trainer.predict`.

        By default, it calls :meth:`~pytorch_lightning.core.lightning.LightningModule.forward`.
        Override to add any processing logic.

        Args:
            batch: Current batch
            batch_idx: Index of current batch
            dataloader_idx: Index of the current dataloader

        Return:
            Predicted output
        """
        return self._post_process(self.validation_step(batch, batch_idx), predict_labels=True)

    def test_step(self, batch, _):  # pylint: disable=arguments-differ
        """Calls validation_step for anomaly map/score calculation.

        Args:
          batch: Input batch
          _: Index of the batch.

        Returns:
          Dictionary containing images, features, true labels and masks.
          These are required in `validation_epoch_end` for feature concatenation.
        """
        return self.validation_step(batch, _)

    def validation_step_end(self, val_step_outputs):  # pylint: disable=arguments-differ
        """Called at the end of each validation step."""
        return self._post_process(val_step_outputs)

    def test_step_end(self, test_step_outputs):  # pylint: disable=arguments-differ
        """Called at the end of each validation step."""
        return self._post_process(test_step_outputs)

    def validation_epoch_end(self, outputs):
        """Compute image-level performance metrics.

        Args:
          outputs: Batch of outputs from the validation step
        """
        self.results.store_outputs(outputs)
        if self.adaptive_threshold:
            threshold, _ = compute_threshold_and_f1_score(self.results.true_labels, self.results.pred_scores)
            self.threshold = torch.Tensor([threshold])
        self.results.evaluate(self.threshold.item())
        self._log_metrics()

    def test_epoch_end(self, outputs):
        """Compute and save anomaly scores of the test set.

        Args:
            outputs: Batch of outputs from the validation step
        """
        self.results.store_outputs(outputs)
        self.results.evaluate(self.threshold.item())
        self._log_metrics()

    def _post_process(self, outputs, predict_labels=False):
        """Compute labels based on model predictions."""
        if "pred_scores" not in outputs and "anomaly_maps" in outputs:
            outputs["pred_scores"] = (
                outputs["anomaly_maps"].reshape(outputs["anomaly_maps"].shape[0], -1).max(axis=1).values
            )
        if predict_labels:
            outputs["pred_labels"] = outputs["pred_scores"] >= self.threshold.item()
        return outputs

    def _log_metrics(self):
        """Log computed performance metrics."""
        for name, value in self.results.performance.items():
            self.log(name=name, value=value, on_epoch=True, prog_bar=True)
