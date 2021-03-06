FROM python:3
WORKDIR /usr/src/app
COPY requirements.txt ./
COPY utils ./utils/
COPY setup.py ./
RUN ls
RUN python3 setup.py build
RUN python3 setup.py install
RUN pip3 install --no-cache-dir -r requirements.txt
# as long as utils is not available via pip, use following two lines
# COPY utils ./
# RUN pip3 install ./utils
