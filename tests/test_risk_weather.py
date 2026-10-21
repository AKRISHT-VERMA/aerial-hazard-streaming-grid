import math
import unittest

from backend.risk import DemoThresholdModel, FEATURE_COLUMNS, FEATURE_COUNT, SVMRiskModel, feature_vector
from backend.weather import DemoWeatherProvider, OpenMeteoWeatherProvider


class FakeEstimator:
    classes_ = ["low", "medium", "high"]

    def predict(self, rows):
        return ["high"]

    def predict_proba(self, rows):
        return [[0.05, 0.15, 0.8]]


class TestRiskAndWeather(unittest.TestCase):
    def features(self):
        return {
            "center_sensor_values": [1, 2, 3, 4, 5, 6],
            "center_sensor_validity": [True] * 6,
            "neighbour_sensor_values": [0] * 6,
            "neighbour_sensor_validity": [False] * 6,
            "weather_temperature": None,
            "weather_wind_speed": None,
            "weather_wind_direction": None,
            "weather_validity": [False] * 3,
        }

    def test_feature_schema_includes_missingness_flags(self):
        vector = feature_vector(self.features())
        self.assertEqual(len(vector), FEATURE_COUNT)
        self.assertEqual(len(FEATURE_COLUMNS), FEATURE_COUNT)
        self.assertEqual(vector[6:12], [1.0] * 6)
        self.assertEqual(vector[24:27], [0.0, 0.0, 0.0])

    def test_non_finite_features_are_rejected(self):
        features = self.features()
        features["center_sensor_values"][0] = math.inf
        with self.assertRaisesRegex(ValueError, "finite"):
            feature_vector(features)

    def test_demo_prediction_is_explicitly_synthetic(self):
        result = DemoThresholdModel(sensor_threshold=100).predict(self.features())
        self.assertTrue(result.synthetic)
        self.assertEqual(result.source, "synthetic-demo-threshold")
        self.assertEqual(result.label, "low")

    def test_trained_svm_adapter_is_not_marked_synthetic(self):
        model = object.__new__(SVMRiskModel)
        model.model = FakeEstimator()
        result = model.predict(self.features())
        self.assertFalse(result.synthetic)
        self.assertEqual(result.source, "trained-svm")
        self.assertEqual(result.risk, 0.8)

    def test_demo_weather_is_labeled_synthetic(self):
        weather = DemoWeatherProvider().get(18.5, 73.8)
        self.assertTrue(weather.synthetic)
        self.assertEqual(weather.source, "synthetic-demo")

    def test_live_weather_rejects_invalid_coordinates_before_network(self):
        with self.assertRaisesRegex(ValueError, "latitude"):
            OpenMeteoWeatherProvider().get(91, 73.8)


if __name__ == "__main__":
    unittest.main()
