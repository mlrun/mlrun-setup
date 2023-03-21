# MLRun Setup

Utility for installing MLRun service locally or over Kubernetes

This utility can be executed from Python or use one of the packaged binaries (one per OS) in the releases tab

to download the binary to your system (on Linux or MacOS):

    curl -sfL https://get.mymlrun.org | bash -
 
to build the binary run: 
 
    pyinstaller -F mlsetup.py


## Usage

Choose the specific installation option (local, docker, kubernetes, and remote), 
and run the command with default or custom options (see `mlsetup COMMAND --help` for option specific help).


```
Usage: mlsetup [OPTIONS] COMMAND [ARGS]...

  MLRun configuration utility

Options:
  --help  Show this message and exit.

Commands:
  clear       Delete the default or specified config .env file
  docker      Deploy mlrun and nuclio services using Docker compose
  get         Print the local or remote configuration
  kubernetes  Install MLRun service on Kubernetes
  latest      Get the latest MLRun version
  local       Install MLRun service as a local process (limited, no UI...
  remote      Connect to remote MLRun service (over Kubernetes)
  set         Set configuration in mlrun default or specified .env file
  start       Start MLRun service, auto detect the best method...
  stop        Stop MLRun service which was started using the start command
```
