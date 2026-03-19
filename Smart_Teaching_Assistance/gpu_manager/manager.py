"""
GPU Manager module for device detection, configuration, and memory management.
"""

import logging
from typing import Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DeviceInfo:
    """Information about the compute device."""
    device_type: str  # "cuda" or "cpu"
    device_name: str
    device_id: int
    memory_total: Optional[int] = None  # bytes
    memory_available: Optional[int] = None  # bytes
    cuda_version: Optional[str] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        result = {
            "device_type": self.device_type,
            "device_name": self.device_name,
            "device_id": self.device_id,
        }
        if self.memory_total is not None:
            result["memory_total_mb"] = round(self.memory_total / (1024 * 1024), 2)
        if self.memory_available is not None:
            result["memory_available_mb"] = round(self.memory_available / (1024 * 1024), 2)
        if self.cuda_version:
            result["cuda_version"] = self.cuda_version
        return result


class GPUManager:
    """Manages GPU detection, configuration, and memory."""
    
    def __init__(self, preferred_device_id: int = 0, use_gpu: bool = True):
        """
        Initialize GPU manager.
        
        Args:
            preferred_device_id: Preferred GPU device ID
            use_gpu: Whether to attempt GPU usage
        """
        self.preferred_device_id = preferred_device_id
        self.use_gpu = use_gpu
        self._device_info: Optional[DeviceInfo] = None
        self._torch_available = False
        self._cuda_available = False
        
        self._detect_hardware()
    
    def _detect_hardware(self):
        """Detect available hardware (GPU/CPU)."""
        try:
            import torch
            try:
                # Fix for Streamlit/Torch watcher crash
                # Do NOT check hasattr() as it triggers the crash!
                torch.classes.__path__ = [] # type: ignore
            except Exception:
                pass
                
            self._torch_available = True
            self._cuda_available = torch.cuda.is_available()
            
            if self._cuda_available and self.use_gpu:
                device_id = min(self.preferred_device_id, torch.cuda.device_count() - 1)
                device_name = torch.cuda.get_device_name(device_id)
                
                # Get memory info
                memory_total = torch.cuda.get_device_properties(device_id).total_mem
                memory_available = memory_total - torch.cuda.memory_allocated(device_id)
                
                # Get CUDA version
                cuda_version = torch.version.cuda
                
                self._device_info = DeviceInfo(
                    device_type="cuda",
                    device_name=device_name,
                    device_id=device_id,
                    memory_total=memory_total,
                    memory_available=memory_available,
                    cuda_version=cuda_version
                )
                
                logger.info(f"GPU detected: {device_name} (CUDA {cuda_version})")
                logger.info(f"Memory: {memory_total / (1024**3):.1f} GB total")
            else:
                self._device_info = DeviceInfo(
                    device_type="cpu",
                    device_name="CPU",
                    device_id=0
                )
                if not self._cuda_available:
                    logger.info("No CUDA-capable GPU detected, using CPU")
                else:
                    logger.info("GPU available but disabled by configuration, using CPU")
                    
        except ImportError:
            self._device_info = DeviceInfo(
                device_type="cpu",
                device_name="CPU (PyTorch not installed)",
                device_id=0
            )
            logger.warning("PyTorch not installed, using CPU fallback")
    
    def get_device_info(self) -> DeviceInfo:
        """
        Get information about the current compute device.
        
        Returns:
            DeviceInfo object with device details
        """
        if self._device_info is None:
            self._detect_hardware()
        return self._device_info
    
    def get_device(self):
        """
        Get the PyTorch device object.
        
        Returns:
            torch.device object for the current device
        """
        if not self._torch_available:
            return None
            
        import torch
        info = self.get_device_info()
        if info.device_type == "cuda":
            return torch.device(f"cuda:{info.device_id}")
        return torch.device("cpu")
    
    def is_gpu_available(self) -> bool:
        """Check if GPU is available and configured."""
        info = self.get_device_info()
        return info.device_type == "cuda"
    
    def clear_memory(self):
        """
        Clear GPU memory cache.
        
        Returns:
            Dictionary with memory stats before and after clearing
        """
        if not self._torch_available:
            return {"status": "pytorch_not_available"}
            
        import torch
        
        result = {"status": "success"}
        
        if torch.cuda.is_available():
            # Record before stats
            before_allocated = torch.cuda.memory_allocated()
            before_cached = torch.cuda.memory_reserved()
            
            # Clear cache
            torch.cuda.empty_cache()
            
            # Collect garbage
            import gc
            gc.collect()
            
            # Record after stats
            after_allocated = torch.cuda.memory_allocated()
            after_cached = torch.cuda.memory_reserved()
            
            result.update({
                "before_allocated_mb": round(before_allocated / (1024 * 1024), 2),
                "before_cached_mb": round(before_cached / (1024 * 1024), 2),
                "after_allocated_mb": round(after_allocated / (1024 * 1024), 2),
                "after_cached_mb": round(after_cached / (1024 * 1024), 2),
                "freed_mb": round((before_cached - after_cached) / (1024 * 1024), 2)
            })
            
            logger.info(f"GPU memory cleared: freed {result['freed_mb']:.2f} MB")
            
            # Update device info
            if self._device_info:
                self._device_info.memory_available = (
                    self._device_info.memory_total - after_allocated
                    if self._device_info.memory_total else None
                )
        else:
            result["status"] = "no_gpu"
            
        return result
    
    def get_memory_info(self) -> Dict:
        """
        Get current GPU memory usage information.
        
        Returns:
            Dictionary with memory statistics
        """
        if not self._torch_available:
            return {"status": "pytorch_not_available"}
            
        import torch
        
        if not torch.cuda.is_available():
            return {"status": "no_gpu"}
        
        device_id = self._device_info.device_id if self._device_info else 0
        
        return {
            "status": "available",
            "device_name": torch.cuda.get_device_name(device_id),
            "total_mb": round(torch.cuda.get_device_properties(device_id).total_mem / (1024 * 1024), 2),
            "allocated_mb": round(torch.cuda.memory_allocated(device_id) / (1024 * 1024), 2),
            "cached_mb": round(torch.cuda.memory_reserved(device_id) / (1024 * 1024), 2),
            "free_mb": round(
                (torch.cuda.get_device_properties(device_id).total_mem - 
                 torch.cuda.memory_allocated(device_id)) / (1024 * 1024), 2
            )
        }
    
    def configure_for_model(self, model_name: str = "default") -> Dict:
        """
        Configure device settings for a specific model type.
        
        Args:
            model_name: Name/type of model to configure for
            
        Returns:
            Configuration dictionary
        """
        info = self.get_device_info()
        
        config = {
            "device": info.device_type,
            "device_id": info.device_id,
            "device_name": info.device_name,
        }
        
        if info.device_type == "cuda" and info.memory_total:
            # Set batch size based on available memory
            memory_gb = info.memory_total / (1024 ** 3)
            if memory_gb >= 8:
                config["batch_size"] = 16
                config["max_image_size"] = 1024
            elif memory_gb >= 4:
                config["batch_size"] = 8
                config["max_image_size"] = 768
            else:
                config["batch_size"] = 4
                config["max_image_size"] = 512
        else:
            config["batch_size"] = 2
            config["max_image_size"] = 512
            
        return config


# Module-level convenience functions

def get_device_info(preferred_device_id: int = 0, use_gpu: bool = True) -> DeviceInfo:
    """
    Get device information.
    
    Args:
        preferred_device_id: Preferred GPU device ID
        use_gpu: Whether to attempt GPU usage
        
    Returns:
        DeviceInfo object
    """
    manager = GPUManager(preferred_device_id, use_gpu)
    return manager.get_device_info()


def clear_memory():
    """
    Clear GPU memory cache.
    
    Returns:
        Dictionary with memory stats
    """
    manager = GPUManager()
    return manager.clear_memory()
