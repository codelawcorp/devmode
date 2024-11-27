# DevMode Tool Documentation

DevMode is a command-line interface (CLI) tool implemented in Python that facilitates development process in Kubernetes environments.

This tool provides several advantages over existing solutions:

- Compared to [Devcontainer](https://containers.dev/), DevMode enables development directly in a development EKS environment, providing greater parity with production compared to local solutions like docker-compose or Minikube.

- While similar in functionality to [DevPod](https://devpod.sh/), DevMode offers a streamlined, transparent codebase that can be readily customized to specific requirements, eliminating opaque error handling.

- Unlike [telepresence](https://www.telepresence.io/), DevMode implements a straightforward configuration model that reduces complexity.

In the context of DevMode, a **workspace** represents the combination of:
1. A Kubernetes deployment (configured with a single replica)
2. A Persistent Volume Claim (PVC) that maintains the persistence of code changes

Note: This workspace concept is distinct from a VSCode workspace, which refers to the local directory containing a Git repository.

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
`devmode --help`  

This tool requires an existing deployemnt to be cloned.  
Start the workspace:  
`devmode start --deployment_name risk-rule-engine-server  --namespace human-risk`  

Note: Git credentials are handled automatically by VSCode's built-in credential management. You do not need to manually configure credentials inside the container:  
For the first time, clone any repo from your private VCS with API token (not SSH) - that will store your credentials in `credentials-helper` store.
Git crentials are propagated automatically by VScode magic. (It sets `GIT_ASKPASS` environment variable).


Connect to the container newly created container with [VScode](https://code.visualstudio.com/docs/devcontainers/attach-container#_attach-to-a-container-in-a-kubernetes-cluster)  
Open the workspace_path in VScode.    
Open terminal in VScode and clone your repo:
`git clone <your_repo_url> .`

🎊  


## Development Instructions
use `.vscode/launch.json` 
`devmode -- --interactive`
`devmode -- --trace	`

