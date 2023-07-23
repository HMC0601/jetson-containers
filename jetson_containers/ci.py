#!/usr/bin/env python3
#
# Tool for generating GitHub Action workflows and self-hosted runners.
#
# Setup/register self-hosted runner service:
#   python3 -m jetson_containers.ci register --token $GITHUB_TOKEN
#
# Generate build/test workflows from packages:
#   python3 -m jetson_containers.ci generate
#
# Generate package readme cards:
#   python3 -m jetson_containers.ci docs --packages=xyz,abc
#
# Generate package list (docs/packages.md)
#   python3 -m jetson_containers.ci docs_index
#
import os
import re
import yaml
import wget
import shutil
import socket
import pprint
import argparse
import subprocess

from jetson_containers import (find_package, find_packages, group_packages, dependant_packages, resolve_dependencies, 
                               L4T_VERSION, JETPACK_VERSION)
  
  
def find_package_workflows(package, root):
    """
    Find all the GitHub Workflows for building a specific package.
    """
    workflow_root = os.path.join(root, '.github/workflows')
    workflows = []
    entries = os.listdir(workflow_root)
    
    for entry in entries:
        entry_path = os.path.join(workflow_root, entry)
        
        if not os.path.isfile(entry_path):
            continue
            
        entry_ext = os.path.splitext(entry)[1]
        
        if not (entry_ext == '.yml' or entry_ext == '.yaml'):
            continue
            
        with open(entry_path) as file:
            workflow = yaml.safe_load(file)
            
        # hacky way to decipher the actual package name from the workflow,
        # since github doesn't allow custom entries and no special chars in names
        if 'run-name' not in workflow:
            continue
            
        tokens = workflow['run-name'].split(' ')
        
        if len(tokens) != 4:
            continue
            
        if package != tokens[1]:
            continue
            
        workflows.append(workflow)
        
    return workflows
        
    
def generate_workflow(package, root, simulate=False):
    """
    Generate the YAML workflow definition for automated container builds for that package
    """
    if not root:
        root = os.path.dirname(os.path.dirname(__file__))
        
    name = package['name']
    workflow_name = f"{name}_jp{JETPACK_VERSION.major}{JETPACK_VERSION.minor}".replace(':','-').replace('.','')
    filename = os.path.join(root, '.github/workflows', f"{workflow_name}.yml")
    
    on_paths = [
        f".github/workflows/{workflow_name}.yml",
        #"jetson_containers/**",
        os.path.join(package['path'].replace(root+'/',''), '*'),
        f"!{os.path.join(package['path'].replace(root+'/',''), 'README.md')}",
    ]

    txt = f"name: \"{workflow_name}\"\n"
    txt += f"run-name: \"Build {name} (JetPack {JETPACK_VERSION.major}.{JETPACK_VERSION.minor})\"\n"  # update find_package_workflows() if this formatting changes
    txt += "on:\n"
    txt += "  workflow_dispatch: {}\n"
    txt += "  push:\n"
    txt += "    branches:\n"
    txt += "      - 'dev'\n"
    txt += "    paths:\n"
    
    for on_path in on_paths:
        txt += f"      - '{on_path}'\n"  
            
    txt += "jobs:\n"
    txt += f"  {workflow_name}:\n"
    txt += f"    runs-on: [self-hosted, jetson, jp{JETPACK_VERSION.major}{JETPACK_VERSION.minor}]\n"
    txt += "    steps:\n"
    txt += "      - run: |\n"
    txt += "         cat /etc/nv_tegra_release \n"
    txt += "         env \n"
    txt += "         echo $PWD \n"
    txt += "         ls -a \n"
    txt += "      - run: |\n"
    txt += "         cd $RUNNER_WORKSPACE \n"
    txt += "         git clone $GITHUB_SERVER_URL/$GITHUB_REPOSITORY || echo 'repo already cloned or another error encountered' \n"
    txt += "         cd jetson-containers \n"
    txt += "         git pull \n"
    txt += "         git checkout $GITHUB_SHA \n"
    txt += "         git status \n"
    txt += "         ls -a \n"
    #txt += f"      - run: ./build.sh --name=runner/ {package['name']}"  # --build-flags='--no-cache' 
    
    print(filename)
    print(txt)

    if not simulate:
        with open(filename, 'w') as file:
            file.write(txt)
            

def register_runner(token, root, repo, labels=[], simulate=False):
    """
    Setup and register this machine as a self-hosted runner with GitHub
    """
    if not args.token:
        raise ValueError(f"--token must be provided from GitHub when registering self-hosted runners")
       
    if not root:
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'runner')
        
    labels.extend([
        'jetson',
        f'jp{JETPACK_VERSION.major}{JETPACK_VERSION.minor}',
        f'jp{JETPACK_VERSION.major}{JETPACK_VERSION.minor}{JETPACK_VERSION.micro}',
        f'r{L4T_VERSION.major}.{L4T_VERSION.minor}',
        f'r{L4T_VERSION}',
        socket.gethostname(),
    ])
    
    labels = [x for x in labels if x]
    
    # github runner package
    run_tar = os.path.join(root, "actions-runner-linux-arm64-2.304.0.tar.gz")
    run_url = "https://github.com/actions/runner/releases/download/v2.304.0/actions-runner-linux-arm64-2.304.0.tar.gz"
    
    if not os.path.isfile(run_tar) and not simulate:
        print(f"-- Installing self-hosted runner under {root}")
        os.makedirs(root, exist_ok=True)
        wget.download(run_url, run_tar)
        shutil.unpack_archive(run_tar, root)
        
    # github cli package
    cli_deb = os.path.join(root, "gh_2.32.0_linux_arm64.deb")
    cli_url = "https://github.com/cli/cli/releases/download/v2.32.0/gh_2.32.0_linux_arm64.deb"
    
    if not os.path.isfile(cli_deb) and not simulate:
        print(f"-- Downloading GitHub CLI package to {cli_deb}")
        wget.download(cli_url, cli_deb)
        cmd = f"sudo dpkg --install {cli_deb}"
        subprocess.run(cmd, executable='/bin/bash', shell=True, check=True) 
        
    # run config command
    cmd = f"cd {root} && "
    cmd += f"./config.sh --url {repo} --token {token} --labels {','.join(labels)} --unattended && sudo ./svc.sh install && sudo ./svc.sh start && sudo ./svc.sh status "

    print(f"-- Configuring self-hosted runner for {repo}\n")
    print(cmd)
    
    if not simulate:
        subprocess.run(cmd, executable='/bin/bash', shell=True, check=True) 
    
    # https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/configuring-the-self-hosted-runner-application-as-a-service
    print(f"\n-- Commands for interacting with the runner service:")
    print(f"  cd {root}\n")
    print(f"  sudo ./svc.sh start      # manually start the service")
    print(f"  sudo ./svc.sh stop       # manually stop the service")
    print(f"  sudo ./svc.sh status     # check the service status")
    print(f"  sudo ./svc.sh uninstall  # remove systemd service\n")
    
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    
    parser.add_argument('cmd', type=str, choices=['generate', 'register'])
    parser.add_argument('packages', type=str, nargs='*', default=[], help='packages to generate workflows for')
    
    parser.add_argument('--root', type=str, default='')
    parser.add_argument('--skip-packages', type=str, default='')
    parser.add_argument('--l4t-versions', type=str, default=str(L4T_VERSION)) #'32.7,35.2'
    parser.add_argument('--simulate', action='store_true')
    
    parser.add_argument('--token', type=str, default='')
    parser.add_argument('--labels', type=str, default='')
    parser.add_argument('--repo', type=str, default='https://github.com/dusty-nv/jetson-containers')
    
    args = parser.parse_args()
    
    #args.packages = re.split(',|;|:', args.packages)
    args.skip_packages = re.split(',|;|:', args.skip_packages)
    args.l4t_versions = re.split(',|;|:', args.l4t_versions)
    args.labels = re.split(',|;|:', args.labels)
    
    print(args)

    packages = find_packages(args.packages, skip=args.skip_packages)
    
    if args.cmd == 'generate':
        for package in packages.values():
            generate_workflow(package, args.root, simulate=args.simulate)
    elif args.cmd == 'register':
        register_runner(args.token, args.root, args.repo, args.labels, simulate=args.simulate)
        