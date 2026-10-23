from __future__ import annotations

import os
from unittest import mock
import unittest

from backend.rt import device


class RtDeviceSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        device._configured = False

    def tearDown(self) -> None:
        device._configured = False

    def test_auto_falls_back_to_cpu_for_unsupported_optix_gpu(self) -> None:
        with mock.patch.dict(os.environ, {"OAT_RT_DEVICE": "auto", "CUDA_VISIBLE_DEVICES": "0"}, clear=False):
            with mock.patch.object(device, "_gpu_compute_capabilities", return_value=[5.0]):
                selected = device.configure_rt_device()
            self.assertEqual(selected, "cpu")
            self.assertEqual(os.environ.get("CUDA_VISIBLE_DEVICES"), "")

    def test_auto_keeps_cuda_for_supported_gpu(self) -> None:
        with mock.patch.dict(os.environ, {"OAT_RT_DEVICE": "auto", "CUDA_VISIBLE_DEVICES": "0"}, clear=False):
            with mock.patch.object(device, "_gpu_compute_capabilities", return_value=[8.6]):
                selected = device.configure_rt_device()
            self.assertEqual(selected, "auto")
            self.assertEqual(os.environ.get("CUDA_VISIBLE_DEVICES"), "0")

    def test_cpu_override_always_hides_cuda(self) -> None:
        with mock.patch.dict(os.environ, {"OAT_RT_DEVICE": "cpu", "CUDA_VISIBLE_DEVICES": "0"}, clear=False):
            selected = device.configure_rt_device()
            self.assertEqual(selected, "cpu")
            self.assertEqual(os.environ.get("CUDA_VISIBLE_DEVICES"), "")


if __name__ == "__main__":
    unittest.main()
