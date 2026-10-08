# Source in WSL before using Hadoop for EarthScape: source config/earthscape-env.sh
export HADOOP_HOME=/opt/hadoop
export HADOOP_MAPRED_HOME=$HADOOP_HOME
export HADOOP_CONF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/hadoop"
export HADOOP_PID_DIR=/home/wisam/hadoop_data/earthscape-pids
export HADOOP_LOG_DIR=/home/wisam/hadoop_data/earthscape-logs
export PATH=$HADOOP_HOME/bin:$HADOOP_HOME/sbin:$PATH
