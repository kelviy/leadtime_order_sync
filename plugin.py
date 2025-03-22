"""
Plugin: Leadtime Order Sync
Integrates Takealot CSV order import with InvenTree Sales Orders and stock sync.
"""

import csv
import datetime
import logging
import os

import requests  
from company.models import Company
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import path, reverse
from order.models import SalesOrder, SalesOrderAllocation, SalesOrderLineItem, SalesOrderShipment
from part.models import Part
from plugin import InvenTreePlugin
from plugin.mixins import APICallMixin, NavigationMixin, SettingsMixin, UrlsMixin
from stock.models import StockItem, StockLocation

# Load Takealot API credentials from environment
TAKEALOT_API_KEY = os.getenv("TAKEALOT_API_KEY")
TAKEALOT_API_BASE_URL = os.getenv(
    "TAKEALOT_API_BASE_URL", "https://seller-api.takealot.com/v2/"
)
TAKEALOT_WAREHOUSE_ID = os.getenv("TAKEALOT_WAREHOUSE_ID")


class LeadtimeOrderSyncPlugin(
    UrlsMixin, NavigationMixin, SettingsMixin, APICallMixin, InvenTreePlugin
):
    """InvenTree plugin for syncing Takealot orders and stock (Leadtime Order Sync)."""

    # Plugin metadata
    NAME = "Leadtime Order Sync"
    SLUG = "leadtimeordersync"
    TITLE = "Leadtime Order Sync"
    DESCRIPTION = "Import Takealot picking list CSV, create Sales Order for 'TakeALot' customer, allocate stock, and sync stock levels to Takealot."
    VERSION = "1.0.0"
    AUTHOR = "Kelvin Wei"
    MIN_VERSION = "0.17.8"  # InvenTree minimum version

    # Navigation: Add link in InvenTree UI navigation bar
    NAVIGATION = [
        {
            "name": "Leadtime Order Sync",
            "link": "plugin:leadtimeordersync:interface",
            "icon": "fas fa-shipping-fast",
        }
    ]
    NAVIGATION_TAB_NAME = "Integrations"
    NAVIGATION_TAB_ICON = "fas fa-plug"

    # Plugin settings (global, configurable via InvenTree plugin settings UI)
    SETTINGS = {
        "DEFAULT_STOCK_LOCATION": {
            "name": "Default Stock Location",
            "description": "Default stock location (name) to allocate stock from",
            "model": "stock.stocklocation",
            "default": "",
            "required": True,
        }
    }

    def setup_urls(self):
        """Define custom URL endpoints for this plugin's views."""
        return [
            path("", login_required(self.interface), name="interface"),
            path(
                "create-order/", login_required(self.create_order), name="create-order"
            ),
            path("sync-stock/", login_required(self.sync_stock), name="sync-stock"),
        ]

    def interface(self, request):
        """Render the main interface for CSV upload and review of matched/unmatched items."""
        context = {
            "plugin": self,
            "title": "Leadtime Order Sync",
            "today": datetime.date.today().strftime("%Y-%m-%d"),
        }

        # Clear any previous session data on initial GET (fresh page load)
        if request.method == "GET":
            if "leadtime_order_sync_data" in request.session:
                del request.session["leadtime_order_sync_data"]
            return render(
                request, "leadtime_order_sync/leadtime_order_sync.html", context
            )

        # Handle CSV upload and date POST
        csv_file = request.FILES.get("csvfile")
        target_date_str = request.POST.get("target_date", "")

        # Attempt to parse target date
        try:
            target_date = (
                datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date()
                if target_date_str
                else datetime.date.today()
            )
        except Exception:
            context["warning"] = "Invalid date format. Using today's date."
            target_date = datetime.date.today()

        # Check for existence of csv_file
        if not csv_file:
            # No file provided – return with an error message
            context["error"] = "Please upload a CSV file."
            return render(
                request, "leadtime_order_sync/leadtime_order_sync.html", context
            )
        
        # Read and parse CSV content
        try:
            data = csv_file.read().decode("utf-8")
        except Exception as e:
            context["error"] = f"Failed to read CSV file: {e}"
            return render(
                request, "leadtime_order_sync/leadtime_order_sync.html", context
            )

        reader = csv.DictReader(data.splitlines())
        expected_cols = {
            "DC",
            "Product Label Number",
            "SKU",
            "TSIN",
            "Product Title",
            "Qty Sending",
            "Qty Required",
        }
        if not expected_cols.issubset(set(reader.fieldnames or [])):
            context["error"] = (
                "CSV file format is incorrect. Expected columns: "
                + ", ".join(expected_cols)
            )
            return render(
                request, "leadtime_order_sync/leadtime_order_sync.html", context
            )

        matched_items = []
        unmatched_items = []
        # Fetch default stock location from settings (if configured)
        location_name = self.get_setting("DEFAULT_STOCK_LOCATION")
        location_obj = None
        if location_name:
            location_obj = StockLocation.objects.filter(pk=location_name).first()
            if not location_obj:
                # Warn if configured location not found in DB
                context["warning"] = (
                    f"Default stock location '{location_name}' not found. Stock allocation will be skipped."
                )
        else:
            context["warning"] = (
                "Default stock location is not configured. Stock allocation will be skipped."
            )

        # Iterate through CSV rows to match parts
        for row in reader:
            sku = row.get("SKU", "").strip().lstrip('"').rstrip('"')
            tsin = row.get("TSIN", "").strip()
            title = row.get("Product Title", "").strip().lstrip('"').rstrip('"')
            dc = row.get("DC", "").strip()
            try:
                qty_required = int(row.get("Qty Required", "").strip() or 0)
            except:
                qty_required = 0
            try:
                qty_sending = int(row.get("Qty Sending", "").strip() or 0)
            except:
                qty_sending = 0

            # Find matching Part by SKU or TSIN (case-insensitive)
            part_obj = None
            if sku:
                part_obj = (
                    Part.objects.filter(IPN__iexact=sku).first()
                    or Part.objects.filter(name__iexact=title).first()
                )
            # TODO: Need to also match by TSIN

            if part_obj:
                # Calculate global stock count
                stock_items = StockItem.objects.filter(part=part_obj, quantity__gt=0)
                
                pre_alloc = 0
                for item in stock_items:

                    sales_allocs = SalesOrderAllocation.objects.filter(item=item)
                    pre_alloc += sum(alloc.quantity for alloc in sales_allocs)

                total_qty = int(sum(item.quantity for item in stock_items))
                available_qty = int(max(total_qty - pre_alloc, 0))
                # Default calculated SoH = current available stock minus Qty Sending (not below 0)
                # Do not allow negative stock on hand
                new_soh = int(max(available_qty - qty_sending, 0))
                # URL to part image (if any)
                # Prepare matched item entry
                image_url = (
                    part_obj.image.url if getattr(part_obj, "image", None) else ""
                )
                matched_items.append(
                    {
                        "part": part_obj.pk,
                        "sku": sku,
                        "tsin": tsin,
                        "name": part_obj.name,
                        "dc": dc,
                        "qty_required": qty_required,
                        "qty_sending": qty_sending,
                        "available": available_qty,
                        "calculated_soh": new_soh,
                        "image_url": image_url,
                    }
                )
            else:
                # Part not found in InvenTree
                unmatched_items.append(
                    {
                        "sku": sku,
                        "tsin": tsin,
                        "product_title": title,
                        "dc": dc,
                        "qty_required": qty_required,
                        "qty_sending": qty_sending,
                    }
                )
        # Save data to session for use in subsequent actions
        request.session["leadtime_order_sync_data"] = {
            "matched_items": matched_items,
            "unmatched_items": unmatched_items,
            "target_date": target_date_str,
        }
        # Populate context for template
        context.update(
            {
                "matched_items": matched_items,
                "unmatched_items": unmatched_items,
                "target_date": target_date,
                "location_name": location_name or "",
                "has_matches": len(matched_items) > 0,
            }
        )

        return render(request, "leadtime_order_sync/leadtime_order_sync.html", context)


    def create_order(self, request):
        """Handle AJAX request to create a Sales Order with allocated stock."""
        data = request.session.get("leadtime_order_sync_data")
        #checks with data.
        # If data contains necessary information. 
        if not data or "matched_items" not in data:
            return JsonResponse(
                {
                    "success": False,
                    "message": "No data to process. Please upload a CSV first.",
                },
                status=400,
            )
        matched_items = data["matched_items"]
        target_date_str = data.get("target_date")
        # data is valid - else defualt value of today
        try:
            target_date = (
                datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date()
                if target_date_str
                else datetime.date.today()
            )
        except:
            target_date = datetime.date.today()
        # Customer is valid
        customer = Company.objects.filter(
            name__iexact="TakeALot", is_customer=True
        ).first()
        if not customer:
            return JsonResponse(
                {
                    "success": False,
                    "message": "Customer 'TakeALot' not found in database.",
                },
                status=400,
            )
       
        # Create new Sales Order
        try:
            order = SalesOrder.objects.create(
                customer=customer, target_date=target_date
            )
        except Exception as e:
            logging.exception("SalesOrder creation failed")
            return JsonResponse(
                {"success": False, "message": f"Failed to create Sales Order: {e}"},
                status=500,
            )

        # Default Stock location is valid else default None for shipment
        location_name = self.get_setting("DEFAULT_STOCK_LOCATION")
        location_obj = (
            StockLocation.objects.filter(pk=location_name).first()
            if location_name
            else None
        )
        #default create on shipment and all items added to shipment
        shipment = SalesOrderShipment.objects.create(delivery_date=target_date, order=order)

        # loop to add line item + allocate stock + allocate to shipment
        try:
            for item in matched_items:
                # add line item
                part_obj = Part.objects.get(pk=item["part"])
                notes = "Imported:\n DC=" + str(item.get("dc")) + "\n Qty Sending=" +str(item.get("qty_sending"))
                qty_required = item.get("qty_required", 0)

                line = SalesOrderLineItem.objects.create(
                    order=order, part=part_obj, quantity=qty_required, notes=notes, sale_price_currency="ZAR", target_date=target_date
                )
                
                # allocate stock and allocate to shipment
                allocate_qty = item.get("qty_sending", 0)
                # skip is default not configured or allocation is 0
                if allocate_qty <= 0 or not location_obj:
                    continue
                #get all stock that is in default location
                stock_qs = StockItem.objects.filter(
                    part=part_obj, location=location_obj, quantity__gt=0
                )
                #attempts to add all stock it found in default location
                for stock_item in stock_qs:
                    

                    pre_alloc = sum(alloc.quantity for alloc in stock_item.allocations.all())

                    available_qty = max(stock_item.quantity - pre_alloc, 0)
                    # add stock quantity capped at allocate value
                    alloc_qty = (
                        available_qty
                        if available_qty < allocate_qty
                        else allocate_qty
                    )

                    if allocate_qty <= 0:
                        break

                    SalesOrderAllocation.objects.create(
                        line=line, item=stock_item, quantity=alloc_qty, shipment=shipment
                    )
                    allocate_qty -= alloc_qty
        except Exception as e:
            logging.exception("Adding line items failed")
            order.delete()
            return JsonResponse(
                {"success": False, "message": f"Error creating order line items: {e}"},
                status=500,
            )

        order_url = f"/order/sales-order/{order.pk}/"
        order_url = request.build_absolute_uri(order_url)
        msg = f"Sales Order {order.reference or order.pk} created with {order.lines.count()} line items."
        if not location_obj:
            msg += " (stock not allocated - no default location configured)."
        else:
            msg += " (stock allocated from default location where available)."

        msg += order_url
        return JsonResponse({"success": True, "message": msg, "url":order_url})


    def sync_stock(self, request):
        """Handle AJAX request to push stock-on-hand updates to Takealot via API (batch update)."""

        data = request.session.get("leadtime_order_sync_data")
        if not data or "matched_items" not in data:
            return JsonResponse(
                {
                    "success": False,
                    "message": "No data to sync. Please upload a CSV first.",
                },
                status=400,
            )
        matched_items = data["matched_items"]

        for item in matched_items:
            field_name = f"soh_part_{item['part']}"
            if field_name in request.POST:
                try:
                    new_soh = int(request.POST[field_name])
                except:
                    continue
                item["calculated_soh"] = max(new_soh, 0)

        batch_payload = []
        for item in matched_items:
            sku = item.get("sku") or ""
            new_soh = item.get("calculated_soh", 0)
            identifier = sku 
            leadtime_stock = [{"merchant_warehouse_id":TAKEALOT_WAREHOUSE_ID, "quantity": new_soh}]
            batch_payload.append({"sku": identifier, "leadtime_stock": leadtime_stock})
        payload = {"requests": batch_payload}

        #debug to check payload
        return JsonResponse( 
                {
                    "success": False, 
                    "message": "This functionality is not yet ready for production"+ "\nWhat would've been sent: " + str(batch_payload)
                },
                status=400
        )


        if not TAKEALOT_API_KEY or not TAKEALOT_API_BASE_URL:
            return JsonResponse(
                {
                    "success": False,
                    "message": "Takealot API credentials not configured. Check .env settings.",
                },
                status=500,
            )

        api_endpoint = TAKEALOT_API_BASE_URL.rstrip("/") + "/stock/create_batch"
        headers = {
            "Authorization": f"Key {TAKEALOT_API_KEY}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.post(
                api_endpoint, headers=headers, json=payload, timeout=10
            )
        except Exception as e:
            logging.exception("Takealot API request failed")
            return JsonResponse(
                {
                    "success": False,
                    "message": f"Failed to connect to Takealot API: {e}",
                },
                status=500,
            )
        if 200 <= response.status_code < 300:
            try:
                resp_data = response.json()
            except:
                resp_data = {}
            batch_id = resp_data.get("batch_id") or resp_data.get("id") or ""
            msg = "Stock levels synced to Takealot successfully"
            if batch_id:
                msg += f" (Batch ID: {batch_id})."
            else:
                msg += "."
            return JsonResponse({"success": True, "message": msg})
        else:
            error_detail = ""
            try:
                error_detail = response.json().get("error") or response.text
            except:
                error_detail = response.text
            logging.error(f"Takealot API error: {response.status_code} {error_detail}")
            return JsonResponse(
                {
                    "success": False,
                    "message": f"Takealot API error {response.status_code}: {error_detail}",
                },
                status=500,
            )
