import pulumi
from pulumi import export
import pulumi_aws as aws


# --- 1. Data Lookups ---

# Get the latest Amazon Linux 2 AMI
# The filter uses 'amazon' alias for owner and the modern 'amzn2' naming convention.
ami = aws.ec2.get_ami(
    most_recent=True,
    owners=["amazon"], 
    filters=[
        {"name":"name","values":["amzn2-ami-hvm-*-x86_64-gp2"]},
        {"name":"virtualization-type","values":["hvm"]},
    ]
)

# Get all available AZs in the current region
azs = aws.get_availability_zones()


# --- 2. Network Infrastructure (VPC, IGW, Route Table) ---

# Create a new VPC
vpc = aws.ec2.Vpc(
    "my-vpc",
    cidr_block="10.0.0.0/16"
)

# Create an Internet Gateway
igw = aws.ec2.InternetGateway(
    "vpc-igw",
    vpc_id=vpc.id
)

# Create the Public Route Table (directs 0.0.0.0/0 traffic to the IGW)
route_table = aws.ec2.RouteTable(
    "public-route-table",
    vpc_id=vpc.id,
    routes=[{
        "cidr_block": "0.0.0.0/0",
        "gateway_id": igw.id,
    }]
)


# --- 3. Multi-AZ Subnets and Association ---

public_subnets = []
# Start the CIDR calculation at index 4 (i+4) to avoid 10.0.1.0/24, 10.0.2.0/24, 10.0.3.0/24
CIDR_START_OFFSET = 4 

for i, az in enumerate(azs.names):
    # Limit to 3 AZs for typical region coverage
    if i >= 3:
        break
        
    subnet = aws.ec2.Subnet(
        f"public-subnet-{az}",
        vpc_id=vpc.id,
        # Change the starting number from i+1 to i + CIDR_START_OFFSET (e.g., 0+4, 1+4, 2+4)
        cidr_block=f"10.0.{i + CIDR_START_OFFSET}.0/24", # Now uses 10.0.4.0/24, 10.0.5.0/24, etc.
        availability_zone=az,
        map_public_ip_on_launch=True
    )
    
    # Associate the subnet to the public route table
    aws.ec2.RouteTableAssociation(
        f"public-rta-{az}",
        route_table_id=route_table.id,
        subnet_id=subnet.id
    )
    public_subnets.append(subnet)


# --- 4. Security Groups ---

# Security Group for EC2 Web Servers (Allows traffic from the Load Balancer/Internet)
group = aws.ec2.SecurityGroup(
    "web-secgrp",
    description='Enable HTTP access to instances',
    vpc_id=vpc.id,
    ingress=[
        # Allow HTTP access from anywhere (for ALB health checks & direct access)
        { 'protocol': 'tcp', 'from_port': 80, 'to_port': 80, 'cidr_blocks': ['0.0.0.0/0'] }, 
        # ICMP/Ping rule
        { 'protocol': 'icmp', 'from_port': 8, 'to_port': 0, 'cidr_blocks': ['0.0.0.0/0'] }, 
    ],
    egress=[
        { 'protocol': '-1', 'from_port': 0, 'to_port': 0, 'cidr_blocks': ['0.0.0.0/0'] },
    ]
)

# Security Group for the Load Balancer (Allows traffic from the Internet)
lb_sec_group = aws.ec2.SecurityGroup(
    "lb-secgrp",
    description='Allow web traffic to load balancer',
    vpc_id=vpc.id,
    ingress=[
        { 'protocol': 'tcp', 'from_port': 80, 'to_port': 80, 'cidr_blocks': ['0.0.0.0/0'] },
    ],
    egress=[
        { 'protocol': '-1', 'from_port': 0, 'to_port': 0, 'cidr_blocks': ['0.0.0.0/0'] },
    ]
)


# --- 5. Load Balancer Setup ---

# Create an Application Load Balancer (ALB)
lb = aws.lb.LoadBalancer(
    "web-app-lb",
    internal=False, # Internet-facing
    load_balancer_type="application",
    subnets=[s.id for s in public_subnets], 
    security_groups=[lb_sec_group.id]
)

# Create a Target Group (where EC2 instances register)
target_group = aws.lb.TargetGroup(
    "web-app-tg",
    port=80,
    protocol="HTTP",
    target_type="instance",
    vpc_id=vpc.id
)

# Create a Listener to forward traffic from the LB (Port 80) to the Target Group
listener = aws.lb.Listener(
    "web-app-listener",
    load_balancer_arn=lb.arn,
    port=80,
    protocol="HTTP",
    default_actions=[{
        "type": "forward",
        "target_group_arn": target_group.arn
    }]
)


# --- 6. Server Creation and Load Balancer Attachment Loop ---
ips = []
hostnames = []

for i, subnet in enumerate(public_subnets):
    # Resolve the AZ name for the user_data script and resource tags
    az_output = subnet.availability_zone 
    
    # Dynamic startup script using .apply()
    user_data = az_output.apply(lambda az: 
        f"""#!/bin/bash 
echo \"Hello, World -- from {az}!\" > index.html
nohup python3 -m http.server 80 &
""")
    
    # Use index (i) for the static resource name
    instance_name = f"web-server-{i}"
    # Use the AZ name for the friendly Name tag
    tag_name = az_output.apply(lambda az: f"web-server-{az}")

    # Create the EC2 Instance (t3.micro is Free Tier eligible)
    server = aws.ec2.Instance(
        instance_name,
        instance_type="t3.micro",
        vpc_security_group_ids=[group.id], # Instance SG
        ami=ami.id,
        subnet_id=subnet.id, 
        user_data=user_data,
        tags={
            "Name": tag_name,
        },
        associate_public_ip_address=True 
    )

    # Attach instance to the Target Group
    attachment = aws.lb.TargetGroupAttachment(
        instance_name, 
        target_group_arn=target_group.arn,
        target_id=server.id, # Target is the EC2 Instance ID
        port=80
    )

    # Collect outputs
    ips.append(server.public_ip)
    hostnames.append(server.public_dns)


# --- 7. Exports ---
export('instance_public_ips', ips)
export('instance_hostnames', hostnames)
export("load_balancer_url", lb.dns_name)
