import pulumi
from pulumi import export
import pulumi_aws as aws

# --- 1. Data Lookups ---

# Get the latest Amazon Linux 2 AMI for HVM instances
ami = aws.ec2.get_ami(
    most_recent=True,
    # Use the alias 'amazon' which Pulumi correctly maps to the AWS owner ID
    owners=["amazon"], 
    filters=[
        # Use the modern Amazon Linux 2 naming convention
        {"name":"name","values":["amzn2-ami-hvm-*-x86_64-gp2"]},
        {"name":"virtualization-type","values":["hvm"]},
    ]
)


# -----------------------------------------------------------
# REPLACING DEFAULT VPC LOOKUP (Because region has no default)
# -----------------------------------------------------------

# Create a new VPC (instead of using default=True lookup)
vpc = aws.ec2.Vpc(
    "my-vpc",
    cidr_block="10.0.0.0/16"
)

# Create an Internet Gateway
igw = aws.ec2.InternetGateway(
    "vpc-igw",
    vpc_id=vpc.id
)

# Create a public subnet
subnet = aws.ec2.Subnet(
    "public-subnet",
    vpc_id=vpc.id,
    cidr_block="10.0.1.0/24",
    map_public_ip_on_launch=True  # Ensures EC2 gets a public IP
)

# Create route table with route to the internet
route_table = aws.ec2.RouteTable(
    "public-route-table",
    vpc_id=vpc.id,
    routes=[{
        "cidr_block": "0.0.0.0/0",
        "gateway_id": igw.id,
    }]
)

# Associate route table to subnet
route_table_association = aws.ec2.RouteTableAssociation(
    "public-rta",
    route_table_id=route_table.id,
    subnet_id=subnet.id
)


# --- 2. Security Group ---
# Enable ping (ICMP Type 8) and HTTP (TCP 80)
group = aws.ec2.SecurityGroup(
    "web-secgrp",
    description='Enable HTTP access',
    # VPC ID is required for security groups, derived from the CREATED VPC
    vpc_id=vpc.id,
    ingress=[
        { 'protocol': 'icmp', 'from_port': 8, 'to_port': 0, 'cidr_blocks': ['0.0.0.0/0'] },
        { 'protocol': 'tcp', 'from_port': 80, 'to_port': 80, 'cidr_blocks': ['0.0.0.0/0'] },
    ],
    egress=[
        # Allow all outbound traffic (default for most security groups)
        { 'protocol': '-1', 'from_port': 0, 'to_port': 0, 'cidr_blocks': ['0.0.0.0/0'] },
    ]
)



# --- 3. Server Creation (Removed unnecessary loop, used static name) ---

# Get the Availability Zone name as a Pulumi Output for the script and tag
az_output = subnet.availability_zone

# The instance resource name MUST be a plain string:
INSTANCE_NAME = "web-server-public"
# The tag name is dynamic, so we use .apply()
TAG_NAME = az_output.apply(lambda az: f"{INSTANCE_NAME}-{az}")


# Create the User Data script dynamically using .apply()
user_data = az_output.apply(lambda az: 
    f"""#!/bin/bash 
echo \"Hello, World -- from {az}!\" > index.html
nohup python3 -m http.server 80 &
""")


server = aws.ec2.Instance( 
    INSTANCE_NAME, # FIX: Use a static, resolved string for the resource name
    instance_type="t3.micro",
    vpc_security_group_ids=[group.id],
    ami=ami.id,
    subnet_id=subnet.id,
    user_data=user_data, # This is correct as it accepts Output values
    tags={
        "Name": TAG_NAME, # This is correct as it accepts Output values
    },
    associate_public_ip_address=True
)

# Create an Elastic IP (EIP)
eip = aws.ec2.Eip(f"eip-{INSTANCE_NAME}", instance=server.id)



# --- 4. Exports ---

pulumi.export('public_ip', eip.public_ip)
pulumi.export('hostname', server.public_dns)
pulumi.export('subnet_id', subnet.id)
pulumi.export('vpc_id', vpc.id)

