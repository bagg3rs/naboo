"""
Export TinyNav PyTorch model weights to NumPy .npz for Pi Zero 2 W inference.

Usage: python3 -m naboo.export_tinynav [--model models/tinynav_*.pt] [--output models/tinynav.npz]
"""
import argparse
import glob
import os

import numpy as np
import torch

from naboo.train_tinynav import TinyNavCNN


def export(model_path, output_path):
    checkpoint = torch.load(model_path, map_location="cpu")
    model = TinyNavCNN()
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()

    # Extract all weights as numpy arrays
    weights = {}
    for name, param in model.named_parameters():
        weights[name] = param.detach().numpy()
        print(f"  {name}: {param.shape}")

    np.savez_compressed(output_path, **weights)
    size_kb = os.path.getsize(output_path) / 1024
    print(f"\nExported {len(weights)} tensors to {output_path} ({size_kb:.1f}KB)")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Export TinyNav to NumPy")
    parser.add_argument("--model", default=None, help="Path to .pt model")
    parser.add_argument("--output", default=None, help="Output .npz path")
    args = parser.parse_args()

    if args.model is None:
        model_dir = os.path.join(os.path.dirname(__file__), "..", "models")
        models = sorted(glob.glob(os.path.join(model_dir, "tinynav_*.pt")))
        if not models:
            print("No model found!")
            return
        args.model = models[-1]

    if args.output is None:
        args.output = os.path.join(os.path.dirname(args.model), "tinynav_pi.npz")

    print(f"Exporting {args.model}")
    export(args.model, args.output)


if __name__ == "__main__":
    main()
