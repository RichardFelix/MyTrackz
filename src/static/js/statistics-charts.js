document.addEventListener("DOMContentLoaded", function () {
  Chart.register(ChartDataLabels);

  // Common configuration for pie charts
  const pieChartConfig = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      datalabels: {
        color: "#D1D5DB",
        font: { size: 12 },
        formatter: (value, ctx) => {
          const total = ctx.dataset.data.reduce((acc, data) => acc + data, 0);
          const percentage = Math.round((value / total) * 100);
          const label = ctx.chart.data.labels[ctx.dataIndex];
          return percentage > 5 ? `${label}\n${percentage}%` : "";
        },
        textAlign: "center",
        textStrokeColor: "rgba(0,0,0,0.5)",
        textStrokeWidth: 2,
        textShadowBlur: 5,
        textShadowColor: "rgba(0,0,0,0.5)",
        padding: 6,
      },
      legend: {
        position: "bottom",
        labels: {
          color: "#D1D5DB",
          padding: 20,
          usePointStyle: true,
          pointStyle: "rectRounded",
          generateLabels: function (chart) {
            const original =
              Chart.overrides.pie.plugins.legend.labels.generateLabels;
            const labels = original.call(this, chart);
            labels.forEach((label, i) => {
              label.text = `${label.text} (${chart.data.datasets[0].data[i]})`;
            });
            return labels;
          },
        },
        margin: { top: 20 },
      },
      tooltip: {
        callbacks: {
          label: function (context) {
            const value = context.raw || 0;
            const total = context.chart.data.datasets[0].data.reduce(
              (a, b) => a + b,
              0
            );
            const percentage = Math.round((value / total) * 100);
            return ` ${value} (${percentage}%)`;
          },
        },
      },
    },
    layout: { padding: { bottom: 10 } },
  };

  // Common configuration for bar charts
  const barChartConfig = {
    responsive: true,
    maintainAspectRatio: false,
    scales: {
      x: {
        stacked: true,
        grid: { color: "rgba(255, 255, 255, 0.1)" },
        ticks: { color: "#D1D5DB" },
      },
      y: {
        stacked: true,
        beginAtZero: true,
        grid: { color: "rgba(255, 255, 255, 0.1)" },
        ticks: { color: "#D1D5DB", precision: 0 },
      },
    },
    plugins: {
      legend: {
        position: "bottom",
        labels: {
          color: "#D1D5DB",
          padding: 20,
          boxWidth: 12,
          boxHeight: 12,
          usePointStyle: true,
          pointStyle: "rectRounded",
          textAlign: "center",
          font: {
            size: 12,
            lineHeight: 0.1,
          },
        },
      },
      tooltip: {
        callbacks: {
          label: function (context) {
            const label = context.dataset.label || "";
            const value = context.raw || 0;
            return `${label}: ${value}`;
          },
        },
      },
      datalabels: {
        color: "#D1D5DB",
        anchor: "center",
        align: "center",
        formatter: (value) => (value > 0 ? value : ""),
        font: { weight: "bold", size: 11 },
        display: 'auto',
      },
    },
  };

  // Helper function to process stacked bar data
  function processBarData(chartData) {
    return {
      labels: chartData.labels,
      datasets: chartData.datasets
        .map((dataset) => ({
          label: dataset.label,
          data: dataset.data,
          backgroundColor: dataset.background_color,
          borderColor: "rgba(255, 255, 255, 0.1)",
          borderRadius: 6,
          borderWidth: 1,
        }))
        .filter((dataset) => dataset.data.some((value) => value > 0)),
    };
  }

  // Helper function to safely initialize charts
  function initializeChartIfExists(elementId, chartType, data, options) {
    const element = document.getElementById(elementId);
    if (element) {
      return new Chart(element.getContext("2d"), {
        type: chartType,
        data: data,
        options: options,
      });
    }
    return null;
  }

  // Create Media Type Distribution Chart
  const mediaTypeDistributionElement = document.getElementById(
    "media_type_distribution"
  );
  if (mediaTypeDistributionElement) {
    const mediaTypeData = JSON.parse(mediaTypeDistributionElement.textContent);
    initializeChartIfExists(
      "mediaTypeChart",
      "pie",
      mediaTypeData,
      pieChartConfig
    );
  }

  // Create Status Distribution Chart
  const statusPieChartElement = document.getElementById(
    "status_pie_chart_data"
  );
  if (statusPieChartElement) {
    const statusPieData = JSON.parse(statusPieChartElement.textContent);
    initializeChartIfExists(
      "statusChart",
      "pie",
      statusPieData,
      pieChartConfig
    );
  }

  // Create Status Stacked Bar Chart
  const statusDistributionElement = document.getElementById(
    "status_distribution"
  );
  if (statusDistributionElement) {
    const statusData = JSON.parse(statusDistributionElement.textContent);
    initializeChartIfExists(
      "statusStackedChart",
      "bar",
      processBarData(statusData),
      barChartConfig
    );
  }

  // Create Score Stacked Bar Chart
  const scoreDistributionElement =
    document.getElementById("score_distribution");
  if (scoreDistributionElement) {
    const scoreData = JSON.parse(scoreDistributionElement.textContent);
    const scoreChartOptions = JSON.parse(JSON.stringify(barChartConfig)); // Deep clone

    // Add score-specific configurations
    scoreChartOptions.scales.x.title = {
      display: true,
      text: "Score",
      color: "#D1D5DB",
      padding: { top: 10, bottom: 0 },
    };

    scoreChartOptions.scales.y.title = {
      display: true,
      text: "Number of Items",
      color: "#D1D5DB",
      padding: { top: 0, left: 10 },
    };

    scoreChartOptions.plugins.title = {
      display: true,
      text: `Average Score: ${scoreData.average_score} (${scoreData.total_scored} items)`,
      color: "#D1D5DB",
      padding: { bottom: 10 },
      font: { size: 14 },
    };

    scoreChartOptions.plugins.tooltip.callbacks.title = function (
      tooltipItems
    ) {
      return `Score: ${tooltipItems[0].label}`;
    };

    // Override datalabels for score chart to ensure zeros are never displayed
    scoreChartOptions.plugins.datalabels = {
      color: "#D1D5DB",
      anchor: "center",
      align: "center",
      formatter: function (value) {
        // Only return a value if it's greater than 0
        return value > 0 ? value : "";
      },
      font: { weight: "bold", size: 11 },
      display: function (context) {
        // Get the current value
        const value = context.dataset.data[context.dataIndex];

        // Get the maximum value in the dataset for comparison
        const maxValue = Math.max(...context.dataset.data);

        // Calculate the relative size (as a percentage of the max)
        const relativeSize = value / maxValue;

        // Only show label if value is significant enough (e.g., at least 20% of max)
        // and greater than zero
        return value > 0 && relativeSize >= 0.2;
      },
    };

    initializeChartIfExists(
      "scoreStackedChart",
      "bar",
      processBarData(scoreData),
      scoreChartOptions
    );
  }
});
