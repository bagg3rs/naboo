"""
TinyNav Training Pipeline — train a tiny CNN for autonomous navigation.

Based on arxiv.org/abs/2603.11071, adapted for Naboo mBot2.

Input:  24×24×10 (10 consecutive depth frames, channel-stacked)
Output: steering [-1, 1] + throttle [0, 1]

Usage: python3 train_tinynav.py [--epochs 50] [--batch 32] [--lr 0.001]
"""

import argparse
import glob
import os
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset, random_split

# ── Config ────────────────────────────────────────────────────────────────────

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "recordings")
MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models")
WINDOW_SIZE = 10    # Number of frames to stack
DEPTH_SIZE = 24     # 24x24 depth resolution


# ── Step 1 & 2: Load and clean data ──────────────────────────────────────────

def load_all_recordings(data_dir):
    """Load all .npz recordings, combine, and clean."""
    files = sorted(glob.glob(os.path.join(data_dir, "*.npz")))
    if not files:
        raise FileNotFoundError(f"No recordings found in {data_dir}")

    all_depths = []
    all_steerings = []
    all_throttles = []

    for f in files:
        d = np.load(f)
        depths = d["depths"]
        steerings = d["steerings"]
        throttles = d["throttles"]

        # Clean: remove stopped frames (throttle ~0) — not useful for navigation
        moving_mask = np.abs(throttles) > 0.05
        depths = depths[moving_mask]
        steerings = steerings[moving_mask]
        throttles = throttles[moving_mask]

        if len(depths) > 0:
            all_depths.append(depths)
            all_steerings.append(steerings)
            all_throttles.append(throttles)

    depths = np.concatenate(all_depths)
    steerings = np.concatenate(all_steerings)
    throttles = np.concatenate(all_throttles)

    print(f"Loaded {len(files)} recordings")
    print(f"Total frames (after removing stopped): {len(depths)}")
    print(f"Steering distribution: left={np.sum(steerings < -0.1)}, "
          f"straight={np.sum(np.abs(steerings) <= 0.1)}, "
          f"right={np.sum(steerings > 0.1)}")
    print(f"Throttle range: [{throttles.min():.2f}, {throttles.max():.2f}]")

    return depths, steerings, throttles


# ── Step 3: Build sliding windows ────────────────────────────────────────────

def build_windows(depths, steerings, throttles, window_size=WINDOW_SIZE):
    """Create sliding windows of consecutive frames.

    Each sample is 10 consecutive depth frames stacked as channels.
    The label is the steering+throttle at the LAST frame of the window.
    This gives the CNN temporal context — it sees what happened over
    the last ~2.5 seconds (10 frames at ~4Hz).
    """
    X = []  # Input: (N, 24, 24, 10)
    y_steer = []
    y_throttle = []

    for i in range(len(depths) - window_size):
        window = depths[i:i + window_size]  # (10, 24, 24)
        # Normalize each frame to 0-1
        w_min, w_max = window.min(), window.max()
        if w_max - w_min > 0:
            window = (window - w_min) / (w_max - w_min)
        else:
            window = np.zeros_like(window)

        X.append(window.transpose(1, 2, 0))  # (24, 24, 10)
        y_steer.append(steerings[i + window_size - 1])
        y_throttle.append(throttles[i + window_size - 1])

    X = np.array(X, dtype=np.float32)
    y_steer = np.array(y_steer, dtype=np.float32)
    y_throttle = np.array(y_throttle, dtype=np.float32)

    print(f"Built {len(X)} sliding windows of {window_size} frames")
    return X, y_steer, y_throttle


# ── Step 4: Data augmentation ────────────────────────────────────────────────

def augment(X, y_steer, y_throttle):
    """Horizontal flip — mirror image and invert steering.

    A left-turn depth pattern flipped becomes a right-turn pattern.
    This doubles the dataset and balances left/right turns.
    """
    X_flip = X[:, :, ::-1, :].copy()  # Flip horizontally
    y_steer_flip = -y_steer.copy()     # Invert steering

    X_aug = np.concatenate([X, X_flip])
    y_steer_aug = np.concatenate([y_steer, y_steer_flip])
    y_throttle_aug = np.concatenate([y_throttle, y_throttle])

    # Add brightness jitter (±20%)
    n = len(X)
    brightness = np.random.uniform(0.8, 1.2, size=(n, 1, 1, 1)).astype(np.float32)
    X_bright = np.clip(X * brightness, 0, 1)
    X_aug = np.concatenate([X_aug, X_bright])
    y_steer_aug = np.concatenate([y_steer_aug, y_steer])
    y_throttle_aug = np.concatenate([y_throttle_aug, y_throttle])

    print(f"Augmented: {len(X)} → {len(X_aug)} samples "
          f"(flip + brightness jitter)")
    return X_aug, y_steer_aug, y_throttle_aug


# ── Step 5: PyTorch Dataset ──────────────────────────────────────────────────

class NavDataset(Dataset):
    def __init__(self, X, y_steer, y_throttle):
        # Convert to channels-first: (N, 10, 24, 24)
        self.X = torch.FloatTensor(X.transpose(0, 3, 1, 2))
        self.y_steer = torch.FloatTensor(y_steer)
        self.y_throttle = torch.FloatTensor(y_throttle)

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        return self.X[idx], self.y_steer[idx], self.y_throttle[idx]


# ── Step 6: TinyNav CNN ──────────────────────────────────────────────────────

class TinyNavCNN(nn.Module):
    """
    TinyNav architecture (~23k parameters):

    Input: (batch, 10, 24, 24)

    Conv2d(10→16, 3×3, stride=2) → 12×12    # Standard conv
    SepConv(16→32, 3×3, stride=2) → 6×6     # Depthwise separable
    SepConv(32→32, 3×3)                       # Same
    SepConv(32→48, 3×3)                       # Same
    Conv2d(48→1, 1×1, sigmoid) → attention    # Spatial attention
    GlobalAvgPool → flatten
    Dense(48→64, relu) + Dropout(0.4)
    ├── Dense(64→1, tanh) → steering [-1, 1]
    └── Dense(64→1, sigmoid) → throttle [0, 1]
    """

    def __init__(self):
        super().__init__()

        # Standard conv
        self.conv1 = nn.Conv2d(WINDOW_SIZE, 16, 3, stride=2, padding=1)

        # Separable convolutions (depthwise + pointwise)
        self.dw2 = nn.Conv2d(16, 16, 3, stride=2, padding=1, groups=16)
        self.pw2 = nn.Conv2d(16, 32, 1)

        self.dw3 = nn.Conv2d(32, 32, 3, padding=1, groups=32)
        self.pw3 = nn.Conv2d(32, 32, 1)

        self.dw4 = nn.Conv2d(32, 32, 3, padding=1, groups=32)
        self.pw4 = nn.Conv2d(32, 48, 1)

        # Spatial attention
        self.attention = nn.Conv2d(48, 1, 1)

        # Classification head
        self.fc1 = nn.Linear(48, 64)
        self.dropout = nn.Dropout(0.4)
        self.fc_steer = nn.Linear(64, 1)
        self.fc_throttle = nn.Linear(64, 1)

    def forward(self, x):
        # Conv block 1
        x = F.relu(self.conv1(x))              # (B, 16, 12, 12)

        # Separable conv 2
        x = F.relu(self.pw2(self.dw2(x)))      # (B, 32, 6, 6)

        # Separable conv 3
        x = F.relu(self.pw3(self.dw3(x)))      # (B, 32, 6, 6)

        # Separable conv 4
        x = F.relu(self.pw4(self.dw4(x)))      # (B, 48, 6, 6)

        # Spatial attention
        att = torch.sigmoid(self.attention(x))  # (B, 1, 6, 6)
        x = x * att                             # Apply attention

        # Global average pooling
        x = x.mean(dim=[2, 3])                 # (B, 48)

        # Dense head
        x = F.relu(self.fc1(x))                # (B, 64)
        x = self.dropout(x)

        steer = torch.tanh(self.fc_steer(x)).squeeze(-1)      # [-1, 1]
        throttle = torch.sigmoid(self.fc_throttle(x)).squeeze(-1)  # [0, 1]

        return steer, throttle


# ── Step 7: Training loop ────────────────────────────────────────────────────

def train(model, train_loader, val_loader, epochs=50, lr=0.001, device="cpu"):
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, patience=5, factor=0.5
    )
    best_val_loss = float("inf")
    best_state = None

    for epoch in range(epochs):
        # Train
        model.train()
        train_loss = 0
        for X, y_s, y_t in train_loader:
            X, y_s, y_t = X.to(device), y_s.to(device), y_t.to(device)
            optimizer.zero_grad()
            pred_s, pred_t = model(X)
            loss_s = F.mse_loss(pred_s, y_s)
            loss_t = F.mse_loss(pred_t, y_t)
            loss = loss_s + loss_t
            loss.backward()
            optimizer.step()
            train_loss += loss.item()
        train_loss /= len(train_loader)

        # Validate
        model.eval()
        val_loss = 0
        val_steer_mae = 0
        val_throttle_mae = 0
        with torch.no_grad():
            for X, y_s, y_t in val_loader:
                X, y_s, y_t = X.to(device), y_s.to(device), y_t.to(device)
                pred_s, pred_t = model(X)
                loss_s = F.mse_loss(pred_s, y_s)
                loss_t = F.mse_loss(pred_t, y_t)
                val_loss += (loss_s + loss_t).item()
                val_steer_mae += torch.abs(pred_s - y_s).mean().item()
                val_throttle_mae += torch.abs(pred_t - y_t).mean().item()

        val_loss /= len(val_loader)
        val_steer_mae /= len(val_loader)
        val_throttle_mae /= len(val_loader)
        scheduler.step(val_loss)

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = model.state_dict().copy()
            marker = " ⭐"
        else:
            marker = ""

        if (epoch + 1) % 5 == 0 or epoch == 0 or marker:
            print(f"Epoch {epoch+1:3d}/{epochs}  "
                  f"train={train_loss:.4f}  val={val_loss:.4f}  "
                  f"steer_mae={val_steer_mae:.3f}  "
                  f"throttle_mae={val_throttle_mae:.3f}{marker}")

    # Restore best model
    if best_state:
        model.load_state_dict(best_state)
    return model, best_val_loss


# ── Step 8: Save model ───────────────────────────────────────────────────────

def save_model(model, model_dir, val_loss):
    os.makedirs(model_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(model_dir, f"tinynav_{timestamp}.pt")
    torch.save({
        "model_state_dict": model.state_dict(),
        "architecture": "TinyNavCNN",
        "input_shape": (WINDOW_SIZE, DEPTH_SIZE, DEPTH_SIZE),
        "val_loss": val_loss,
        "params": sum(p.numel() for p in model.parameters()),
    }, path)
    print(f"\nModel saved to {path}")
    print(f"Parameters: {sum(p.numel() for p in model.parameters()):,}")
    print(f"File size: {os.path.getsize(path)/1024:.1f}KB")
    return path


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Train TinyNav CNN")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--lr", type=float, default=0.001)
    parser.add_argument("--data-dir", default=DATA_DIR)
    parser.add_argument("--model-dir", default=MODEL_DIR)
    args = parser.parse_args()

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Device: {device}")
    print(f"=" * 60)

    # Step 1 & 2: Load and clean
    print("\n📦 Loading recordings...")
    depths, steerings, throttles = load_all_recordings(args.data_dir)

    # Step 3: Build windows
    print("\n🪟 Building sliding windows...")
    X, y_steer, y_throttle = build_windows(depths, steerings, throttles)

    # Step 4: Augment
    print("\n🔄 Augmenting data...")
    X, y_steer, y_throttle = augment(X, y_steer, y_throttle)

    # Step 5: Split
    dataset = NavDataset(X, y_steer, y_throttle)
    train_size = int(0.8 * len(dataset))
    val_size = len(dataset) - train_size
    train_set, val_set = random_split(dataset, [train_size, val_size])
    train_loader = DataLoader(train_set, batch_size=args.batch, shuffle=True)
    val_loader = DataLoader(val_set, batch_size=args.batch)
    print(f"\n📊 Train: {train_size}, Validation: {val_size}")

    # Step 6: Build model
    model = TinyNavCNN().to(device)
    params = sum(p.numel() for p in model.parameters())
    print(f"\n🧠 TinyNav CNN: {params:,} parameters")

    # Step 7: Train
    print(f"\n🏋️ Training for {args.epochs} epochs...")
    print("-" * 60)
    model, best_val = train(model, train_loader, val_loader,
                            epochs=args.epochs, lr=args.lr, device=device)

    # Step 8: Save
    print("\n" + "=" * 60)
    save_model(model, args.model_dir, best_val)
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
