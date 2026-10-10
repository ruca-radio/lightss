echo "assass" | sudo -S lsof -ti:8123 | tee /dev/tty | xargs -r sudo kill -9
