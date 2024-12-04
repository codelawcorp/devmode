# DevMode Tool Documentation

DevMode is a command-line interface (CLI) tool implemented in Python that facilitates development process in Kubernetes environments.  

DevMode enables development directly in a `dev` EKS environment, providing greater parity with production compared to local solutions like docker-compose or Minikube.  

It provides **more control**, **less errors**, **faster iterations**.


#### More control
Better to say - allows to focus on things you should really control instead of wasting effort on EKS environment simulation.   


#### Less errros
Compared to `docker-compose`, developing right inside Kubernetes provides a **lesser scope of potential errors**. Locally there are to many things you have to do imitate parity. Here is the list of pitfalls added when you develop locally:
    - docker-compose:
        - yaml syntax errors
        - Docker VM configuration discrepancies
        - 
    - AWS IAM access:
        - The mechanism of passing credentials is different and causes errors time-to-time.
        - Develer's IAM role might be more powerful than pods's IAM role causing autorization errors later.
    - Kubernetes controlplane:
        - If applicaiton communicates to controlplane it uses your local kubeconfig with your own permissions, which often has a broader set of permissions than the real serviceaccount.
        - Kubernetes mounts serviceaccont token into well-known pat, which requires a workaround in local devlopment: Code includes `if` statements which runs exclusively in `local`.
    - AWS Network and DNS:
        - Not all AWS VPC private DNS names are resolved locally.
        - Even with VPN not all network resources are reachable due to SG.
    - Kubernetes Network and DNS:
        - You can not query Kubernetes Service by DNS name. You use docker-compose service name instead which makes developer to write risky `if` statements in code.
        - Pods', services' private IPs can be accessed locally if needed.
    - Reading secrets and variables:
        - You have to stay vigilant that docker-compose env vars and secret match those in Helm.
        - If passing secrets and variables via file (recommended way), mount paths must match locally and inside pod.

#### Faster iterations
`prod` <- `stg` <- `dev` <- ~~`local`~~  
You do not have to wait for CI/CD to complete in `dev` environment to test because you are developing in `dev` environment.



### Alternatives
- Compared to [Devcontainer](https://containers.dev/), same disadvantages as docker-compose

- While similar in functionality to [DevPod](https://devpod.sh/), DevMode offers a streamlined, transparent codebase that can be readily customized to specific requirements, eliminating opaque error handling. Because DevMode's code is a relatively small script, you can easily understand what it does and modify it to your needs / contribute.

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
ln -s "$(pwd)/devmode.py" ~/scripts/devmode
echo "export PATH=$PATH:~/scripts" >> ~/.zshrc
```

## Usage
`aws eks update-kubeconfig --alias "mc-elevate-dev-eu-west-2" --name main --profile "mc-elevate-dev" --region "eu-west-2" --kubeconfig ~/.kube/"mc-elevate-dev-eu-west-2"`  Configure EKS dev kubeconfig.
`aws sso login --profile mc-elevate-prod`  - Authenticate to AWS (EKS).  


DevMode tool requires an existing deployemnt to be cloned.  
`devmode --help`  

Start the workspace:  
`devmode start --deployment_name risk-rule-engine-server  --namespace human-risk --workspace_name name-surname`  

Note: Git credentials are handled automatically by VSCode's built-in credential management. You do not need to manually configure credentials inside the container:  
For the first time, clone any repo from your private VCS with API token (not SSH) - that will store your credentials in `credentials-helper` store.
Git crentials are propagated automatically by VScode magic. (It sets `GIT_ASKPASS` environment variable).


Connect to the container newly created container with [VScode](https://code.visualstudio.com/docs/devcontainers/attach-container#_attach-to-a-container-in-a-kubernetes-cluster)  
Open the `workspace_path` in VScode.    
Open terminal in VScode and clone your repo:
`git clone <your_repo_url> .`

🎊  



## Development Instructions
- Install git hooks. Run this command `git config core.hooksPath .githooks`. 

Run with `LOG_LEVEL=debug devmode list` to see debug logs.   

use `.vscode/launch.json` 
`devmode -- --interactive`
`devmode -- --trace	`

### Contribution
<!-- - Configure Jenkinsfile and Github Action  -->
- Refactor, create `Workspace` class.
- Show informative error when aws logged out.
- Change selector to avoid interering with existing service. Find service, ingress and create clones with `-devmode` prefix.  
- Enable creating a pod's clone. Now you can clone only Deployment.
- Enable non gp2 StorageClass (or use default).
- Enable Specifying a custom image.
- "Don't elevate privileges" flag
- **Super contribution** - launch th.e image built from Dockerfile.


### Testing
It uses `pytest` and a custom fixture which creates a temporary namespace.  
EKS cluster must be connected.