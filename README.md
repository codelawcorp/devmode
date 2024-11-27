# README
## Installation Instructions

### Install Dependencies
`pip3 install -r requirements.txt` (globally)

### Add to PATH
On macOS, you can add the script to your PATH by:

Add this tool to your PATH for easy access:
```bash
mkdir -p ~/scripts
ln -s "$(pwd)/devmode" ~/scripts/devmode
echo "export PATH=$PATH:~/scripts/devmode" >> ~/.zshrc
```

## Usage

### List Workspaces
`devmode list`


## Development Instructions
`devmode -- --interactive`
`devmode -- --trace	`