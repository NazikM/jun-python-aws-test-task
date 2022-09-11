from threading import Thread
from time import sleep

import boto3
import os

from botocore.exceptions import ClientError
# from logging import basicConfig, getLogger, INFO
#
# basicConfig(level=INFO)
# logger = getLogger(__name__)


class Proxy:
    class_instance = None

    def __new__(cls, logger, instance_type="ec2", region_name="eu-central-1"):
        if not hasattr(cls, 'instance'):
            cls.class_instance = super().__new__(cls)
        return cls.class_instance

    def __init__(self, logger, instance_type="ec2", region_name="eu-central-1"):
        self.ec2_client = boto3.client(instance_type,
                                       region_name=region_name,
                                       aws_access_key_id=os.environ["AWS_SERVER_PUBLIC_KEY"],
                                       aws_secret_access_key=os.environ["AWS_SERVER_SECRET_KEY"]
                                       )

        # Format - [instanceId, ...]
        self.instances = []
        # Format - dict(id: ..., public_ip: ..., state: ..., can_live: bool)
        self.instance_in_use = None
        # Creates security group
        self.security_group_id = None
        self.logger = logger

    def initialize_security_group(self):
        response = self.ec2_client.describe_vpcs()
        vpc_id = response.get('Vpcs', [{}])[0].get('VpcId', '')

        try:
            response = self.ec2_client.create_security_group(GroupName='proxy_security_group',
                                                             Description='Group for test task',
                                                             VpcId=vpc_id)
            self.security_group_id = response['GroupId']
            self.logger.info(f'Security Group Created {self.security_group_id} in vpc {vpc_id}.')

            data = self.ec2_client.authorize_security_group_ingress(
                GroupId=self.security_group_id,
                IpPermissions=[
                    {'IpProtocol': "-1",
                     'FromPort': 8080,
                     'ToPort': 8080,
                     'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}])
            self.logger.info(f'Ingress Successfully Set {data}')
            data = self.ec2_client.authorize_security_group_egress(
                GroupId=self.security_group_id,
                IpPermissions=[
                    {'IpProtocol': "-1",
                     'FromPort': 8080,
                     'ToPort': 8080}])
            self.logger.info(f'Egress Successfully Set {data}')
        except ClientError as e:
            self.logger.error(e)

    def proxy_life_timer(self):
        """
            Created for every proxy in use. Proxy life timer is 5min. After that it should be switched.
        """
        instance_id = self.instance_in_use["id"]
        for i in range(300):
            sleep(1)
            if not self.instance_in_use or self.instance_in_use["id"] != instance_id:
                return
        self.instance_in_use["can_live"] = False

    def create_instance(self):
        """
        Create a new instance in aws
        :return: instance_id
        """
        instances = self.ec2_client.run_instances(
            ImageId="ami-02e2ac9bd504f5364",
            MinCount=1,
            MaxCount=1,
            InstanceType="t2.micro",
            SecurityGroupIds=[
                self.security_group_id,
            ]
        )
        instance_id = instances['Instances'][0]['InstanceId']
        self.logger.info(f"Instance {instance_id} successfully created.")
        return instance_id

    def get_instance_data(self, instance_id, state_only=False):
        """
        :return: All data about instance in use.
        """
        reservations = self.ec2_client.describe_instances(InstanceIds=[instance_id]).get("Reservations")

        for reservation in reservations:
            for instance in reservation['Instances']:
                if not state_only:
                    return {
                        "publicIpAddress": instance["PublicIpAddress"],
                        "state": instance["MetadataOptions"]["State"]
                    }
                return {"state": instance["MetadataOptions"]["State"]}

    def terminate_instance(self):
        """
            Delete instance in use
        """
        response = self.ec2_client.terminate_instances(InstanceIds=[self.instance_in_use['id']])
        self.logger.info(f"Instance {self.instance_in_use['id']} successfully terminated.")
        self.logger.info(response)

    def fill_instances(self):
        for i in range(2 - len(self.instances)):
            self.instances.append(self.create_instance())

    def set_instance_in_use(self):
        """
            Set instance in use. What means set the current proxy in use.
        """
        instance_id = self.instances[0]
        can_live = True

        data = self.get_instance_data(instance_id)
        while data.get('state') != "applied":
            sleep(1)
            data = self.get_instance_data(instance_id)
        self.logger.info("Waiting 60 sec")
        sleep(60)
        public_ip = data["publicIpAddress"]
        state = data["state"]

        self.instance_in_use = {
            "id": instance_id,
            "public_ip": public_ip,
            "state": state,
            "can_live": can_live
        }
        Thread(target=self.proxy_life_timer, daemon=True).start()

    def get_proxy(self):
        """
        :return: Proxy urls (http, https)
        """
        if not self.instance_in_use:
            self.initialize_security_group()
            self.fill_instances()
            self.set_instance_in_use()

        if not self.instance_in_use['can_live']:
            self.switch_proxy()

        return {
            'http': f'http://{self.instance_in_use["public_ip"]}:8080',
        }

    def switch_proxy(self):
        """
            Switch current proxy to another one.
        """
        self.terminate_instance()
        self.instances.remove(self.instance_in_use['id'])
        self.instance_in_use = None
        self.set_instance_in_use()
        self.fill_instances()
        self.logger.info("Switched proxy")

    def delete_everything(self):
        """
            Deleting instances and security_group in AWS.\n
            ( This function should be called after work is finished. )
        """
        try:
            self.ec2_client.terminate_instances(InstanceIds=self.instances)
            self.logger.info("Instances Deleted")
            sleep(120)
            self.ec2_client.delete_security_group(GroupId=self.security_group_id)
            self.logger.info('Security Group Deleted')
        except ClientError as e:
            self.logger.error(e)


# For testing
# proxy = Proxy(logger)
# proxy_data = proxy.get_proxy()
# print(proxy_data)
# page = requests.get("http://mt0.google.com/vt?lyrs=s&x=268396&y=390159&z=20", proxies=proxy_data)
# print(page.content[:100], "\n\n\n")
# proxy.switch_proxy()
# proxy_data = proxy.get_proxy()
# print(proxy_data)
# page = requests.get("http://mt0.google.com/vt?lyrs=s&x=268396&y=390159&z=20", proxies=proxy_data)
# print(page.content[:100], "\n\n\n")
# proxy.delete_everything()
