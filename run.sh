#!/bin/bash

brew install git

git clone https://github.com/guardianai-1/Guardian-AI.git
cd Guardian-AI


curl -LsSf https://astral.sh/uv/install.sh | sh

chmod +x ./scripts/run_with_uv.sh

./scripts/run_with_uv.sh
