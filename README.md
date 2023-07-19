# MLRun Setup

Utility for installing MLRun service locally or over Kubernetes

This utility can be executed from Python or use one of the packaged binaries (one per OS) in the releases tab

### Using the Python version 

The Python code require two packages (`click` and `dotenv`), make sure they are installed before executing the script.

Installing the python script:

```
curl https://raw.githubusercontent.com/mlrun/mlrun-setup/development/mlsetup.py > mlsetup.py
chmod u+x mlsetup.py
pip install click~=8.0.0 python-dotenv~=0.17.0
```

Once its installed run `./mlsetup.py [COMMAND]` (for example `./mlsetup.py kubernetes`)

### Using the binary version

to download the binary to your system (on Linux or MacOS):

    curl -sfL https://get.mymlrun.org | bash -

## Usage

Choose the specific installation option (local, docker, kubernetes, and remote), 
and run the command with default or custom options (see `mlsetup COMMAND --help` for option specific help).

> When using the python library replace `mlsetup` with `.\mlsetup.py`.

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

### Install with Docker Compose

```
Usage: mlsetup docker [OPTIONS]

  Deploy mlrun and nuclio services using Docker compose

Options:
Options:
  -j, --jupyter TEXT        deploy Jupyter container, can provide jupyter
                            image as argument
  -d, --data-volume TEXT    host path prefix to the location of db and
                            artifacts
  --volume-mount TEXT       container mount path (of the data-volume), when
                            different from host data volume path
  -a, --artifact-path TEXT  default artifact path (if not in the data volume)
  --foreground              run process in the foreground (not as a daemon)
  -p, --port INTEGER        MLRun port to listen on
  -e, --env-vars TEXT       additional env vars, e.g. -e
                            AWS_ACCESS_KEY_ID=<key-id>
  -f, --env-file TEXT       path to the mlrun .env file (defaults to
                            '~/.mlrun.env')
  --tag TEXT                MLRun version tag
  --milvus                  Install Milvus vector database
  --compose-file TEXT       path to save the generated compose.yaml file
  -v, --verbose             verbose log
  --simulate                simulate install (print commands vs exec)
  --help                    Show this message and exit.
```

### Install with Kubernetes

```
Usage: mlsetup.py kubernetes [OPTIONS]

  Install MLRun service on Kubernetes

Options:
  -n, --name TEXT           helm deployment name
  --namespace TEXT          kubernetes namespace
  -r, --registry-args TEXT  docker registry args, can be a kind string (local,
                            docker, ..) or a set of key=value args e.g. -r
                            username=joe -r password=j123 -r
                            email=joe@email.com, supported keys: kind,server,u
                            sername,password,email,url,secret,push_secret
  -o, --options TEXT        optional services to enable, supported services:
                            spark,monitoring,jupyter,pipelines
  -d, --disable TEXT        optional services to disable, supported services:
                            spark,monitoring,jupyter,pipelines
  -s, --set TEXT            Additional helm --set commands, accept multiple
                            --set options
  --external-addr TEXT      external ip/dns address
  --tag TEXT                MLRun version tag
  -f, --env-file TEXT       path to the mlrun .env file (defaults to
                            '~/.mlrun.env')
  -e, --env-vars TEXT       additional env vars, e.g. -e
                            AWS_ACCESS_KEY_ID=<key-id>
  -v, --verbose             verbose log
  --simulate                simulate install (print commands vs exec)
  --chart-ver TEXT          MLRun helm chart version
  -j, --jupyter TEXT        deploy Jupyter container, can provide jupyter
                            image as argument
  --help                    Show this message and exit.
```

### Uninstall

```
Usage: mlsetup stop [OPTIONS]

  Stop MLRun service which was started using the start command

Options:
  -f, --env-file TEXT    path to the mlrun .env file (defaults to
                         '~/.mlrun.env')
  -d, --deployment TEXT  deployment mode: local | docker | kuberenetes
  -c, --cleanup          delete the specified or default env file
  -f, --force            force stop
  -v, --verbose          verbose log
  --help                 Show this message and exit.
```

## Build

 
to build the binary run: 
 
    pyinstaller -F mlsetup.py
