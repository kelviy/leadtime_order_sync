# Leadtime Order Sync - Inventree Plugin for Syncing TakeALot Leadtime Orders
Plugin that extends inventree's functionality so that leadtime orders can quickly be imported into Inventree's Database. The process can be started by uploading a csv file that contains
all necessary information. 

The Plugin can:
1. Create Sale Orders and related Delivery shipments from csv file
2. Update Leadtime SoH using TakeALot API

![image](https://github.com/user-attachments/assets/6fb1d058-d04d-47a5-b867-da40544e8351)

# Pre-config
1. An existing inventree server must be running and plugin is added and enabled.
2. It is assumed that TakeALot related information about a part is stored as the part's parameter
3. TakeALot Customer is added to Inventree's Database
1. A .env file is created that contains TakeALot API credentials 
