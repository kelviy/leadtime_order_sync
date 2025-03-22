// Run cp_static after editing static folder content. Correct content is in another place for docker installation. Hence the script will move to correct place.
// cp_static.sh to be run from base plugin folder (leadtime_order_sync)
$(function () {
  // CSRF token from cookie
  function getCsrfToken() {
    return document.cookie.match(/csrftoken=([^;]+)/)?.[1] || "";
  }

  // Helper: append a log message to the actionLogs div
  function logMessage(message, type, url = null) {
    const alertClass = type === "success" ? "alert-success" : "alert-danger";
    // Append optional URL if provided
    const urlPart = url ? ` <a href="${url}" target="_blank">(View)</a>` : "";
    const time = new Date().toLocaleTimeString();
    $("#actionLogs").append(
      `<div class="alert ${alertClass} p-2 my-1"><strong>[${time}]</strong> ${message}${urlPart}</div>`,
    );
  }

  // Get the endpoints from the data attributes of the container
  const createUrl = $("#actionContainer").data("create-url");
  const syncUrl = $("#actionContainer").data("sync-url");

  // Create Sales Order button click
  $("#createOrderBtn").click(function () {
    if ($(this).prop("disabled")) return;
    const $btn = $(this);
    $btn.prop("disabled", true);
    $.ajax({
      url: createUrl,
      type: "POST",
      data: $("#orderForm").serialize(),
      headers: { "X-CSRFToken": getCsrfToken() },
      success: function (response) {
        if (response.success) {
          logMessage(
            response.message || "Sales order created successfully.",
            "success",
            response.order_url || null,
          );
        } else {
          logMessage(
            response.message || "Failed to create sales order.",
            "error",
          );
        }
      },
      error: function (xhr) {
        const msg = xhr.responseJSON?.message || "Error creating sales order.";
        logMessage(msg, "error");
      },
      complete: function () {
        $btn.prop("disabled", false);
      },
    });
  });

  // Sync Stock button click
  $("#syncStockBtn").click(function () {
    if ($(this).prop("disabled")) return;
    const $btn = $(this);
    $btn.prop("disabled", true);
    $.ajax({
      url: syncUrl,
      type: "POST",
      data: $("#orderForm").serialize(),
      headers: { "X-CSRFToken": getCsrfToken() },
      success: function (response) {
        if (response.success) {
          logMessage(
            response.message || "Stock synced successfully.",
            "success",
          );
        } else {
          logMessage(
            response.message || "Failed to sync stock to TakeALot.",
            "error",
          );
        }
      },
      error: function (xhr) {
        const msg =
          xhr.responseJSON?.message || "Error syncing stock to TakeALot.";
        logMessage(msg, "error");
      },
      complete: function () {
        $btn.prop("disabled", false);
      },
    });
  });
});
