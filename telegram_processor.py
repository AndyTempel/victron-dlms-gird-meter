import logging
import os

import yaml

try:
	from lxml import etree

	USES_LXML = True
except ImportError:
	import xml.etree.ElementTree as etree

	USES_LXML = False

logging.basicConfig(
	level=logging.WARNING,
	format="%(asctime)s [%(levelname)s] %(message)s"
)


class TelegramProcessor:
	def __init__(self) -> None:
		# Load configs once during initialization
		config_path = os.path.join(os.path.dirname(__file__), "telegrams/default.yml")
		with open(config_path, "r") as f:
			self._default_config = yaml.safe_load(f)

		self.available_telegrams = {}
		self.config = self._default_config
		self.selected_telegram = None
		self._is_telegram_length_unique = {}
		self._telegram_structure_hash = {}

		# Load all telegram definitions at once
		telegram_dir = os.path.join(os.path.dirname(__file__), "telegrams")
		for filename in os.listdir(telegram_dir):
			if filename.endswith(".yml") and filename != "default.yml":
				with open(os.path.join(telegram_dir, filename), "r") as f:
					content = yaml.safe_load(f)
					if "info" in content and "id" in content["info"]:
						self.available_telegrams[content["info"]["id"]] = content

	@classmethod
	def use_telegram(cls, telegram_id):
		self = cls()
		if telegram_id not in self.available_telegrams:
			avail_tel = "\n	".join(self.available_telegrams.keys())
			raise ValueError(
				f"Telegram with id {telegram_id} not found!\n  "
				f"Available telegrams:\n	{avail_tel}"
			)

		# Store telegram config and pre-compute lookup tables
		self.selected_telegram = self.available_telegrams[telegram_id]
		self.config = (
			self._default_config.copy()
		)  # Use copy to avoid modifying original
		self.config.update(self.selected_telegram)

		# Pre-compute telegram lookup data
		for telegram in self.selected_telegram["telegrams"]:
			telegram_length = telegram["length"]
			self._is_telegram_length_unique[telegram_length] = (
				telegram_length not in self._is_telegram_length_unique
			)

			# Pre-compute structure hash for faster matching
			structure_hash = hash("".join(x["type"] for x in telegram["contents"]))
			self._telegram_structure_hash[structure_hash] = telegram["name"]

		return self

	def process_xml(self, xml):
		# Parse XML - this is an expensive operation we can't avoid
		if USES_LXML:
			root = etree.fromstring(
				xml, parser=etree.XMLParser(recover=True, remove_comments=True)
			)
		else:
			root = etree.fromstring(xml, parser=etree.XMLParser())

		if root.tag != "Structure":
			raise ValueError("Invalid XML format")

		# Find matching telegram structure
		telegram_length = int(root.attrib.get("Qty"), 16)
		telegram_structure = self._get_structure(telegram_length, root)
		if telegram_structure is None:
			logging.error(f"Telegram with length {telegram_length} not found")
			raise ValueError("Unknown telegram")

		# Pre-allocate result dictionary with expected size
		payload = {}

		# Parse elements more efficiently
		for i, child in enumerate(root):
			try:
				# Direct assignment instead of update reduces dict operations
				element_data = self._parse_element(i, child, telegram_structure)
				if element_data:
					name, value = element_data
					payload[name] = value
			except ValueError as e:
				logging.error(f"Error parsing element {i}: {e}")
				raise e

		# Apply transformations from config
		transformations = self.config.get("transformations", [])
		if transformations:
			for transform in transformations:
				result = self._do_transform(payload, transform)
				if result:
					key, val = result
					payload[key] = val

		# Apply generic transforms (power calculations)
		self._generic_transform(payload)

		return {"name": telegram_structure["name"], "data": payload}

	def _get_structure(self, telegram_length, root):
		# Fast path: unique length match
		if (
			telegram_length in self._is_telegram_length_unique
			and self._is_telegram_length_unique[telegram_length]
		):
			for definition in self.selected_telegram["telegrams"]:
				if definition["length"] == telegram_length:
					return definition

		# Slower path: check structure hash
		tag_hash = hash("".join(x.tag for x in root))
		telegram_name = self._telegram_structure_hash.get(tag_hash)
		if telegram_name:
			for definition in self.selected_telegram["telegrams"]:
				if definition["name"] == telegram_name:
					return definition

		return None

	def _parse_element(self, position, child, telegram_structure):
		# Find matching element definition by position
		element = None
		for e in telegram_structure["contents"]:
			if e["position"] == position:
				element = e
				break

		if not element or "type" not in element:
			raise ValueError("Element missing type in definition")

		if child.tag != element["type"]:
			raise ValueError(f"Expected tag {element['type']} but got {child.tag}")

		# More efficient value parsing
		element_type = element["type"]
		value_attr = child.attrib["Value"]

		if element_type == "OctetString":
			# Avoid intermediate bytes object when possible
			value = bytes.fromhex(value_attr).decode("ascii")
		elif element_type in ["UInt32", "UInt16", "UInt8", "Enum"]:
			value = int(value_attr, 16)
		elif element_type == "Boolean":
			value = value_attr == "True"
		else:
			logging.warning(f"Unsupported type {element_type}")
			value = None

		# Return tuple instead of dict to avoid allocation
		return element["name"], value

	def _do_transform(self, payload, transform):
		transform_type = transform["type"]
		key = transform["key"]
		key_value = payload.get(key)

		if key_value is None:
			return None

		output_key = key  # Default to the same key
		output_val = None

		# Process transformation based on type
		if transform_type == "MULTIPLY":
			output_val = key_value * transform["value"]
		elif transform_type == "ADD":
			output_val = key_value + transform["value"]
		elif transform_type == "SUBTRACT":
			output_val = key_value - transform["value"]
		elif transform_type == "DIVIDE":
			output_val = key_value / transform["value"]
		elif transform_type == "REPLACE":
			output_val = transform["value"]
		elif transform_type == "TO_INTEGER":
			output_val = int(key_value)
		elif transform_type == "TO_STRING":
			output_val = str(key_value)
		elif transform_type == "TO_FLOAT":
			output_val = float(key_value)
		elif transform_type == "MULTIPLY_IF_KEY":
			transform_key = transform["transform_key"]
			output_key = transform_key
			val_to_transform = payload.get(transform_key)

			if val_to_transform is None:
				return None

			operand = transform["operand"]
			compare_value = transform["value"]
			multiplier = transform["multiplier"]

			# Optimize comparison operations
			if (
				(operand == "GT" and key_value > compare_value)
				or (operand == "GTE" and key_value >= compare_value)
				or (operand == "LT" and key_value < compare_value)
				or (operand == "LTE" and key_value <= compare_value)
				or (operand == "EQ" and key_value == compare_value)
				or (operand == "NEQ" and key_value != compare_value)
			):
				output_val = val_to_transform * multiplier

		return (output_key, output_val) if output_val is not None else None

	def _generic_transform(self, payload):
		# Optimize phase power calculations
		for phase in ("L1", "L2", "L3"):
			power_total_key = f"ACTIVE_POWER_TOTAL_{phase}"
			if power_total_key not in payload:
				import_key = f"ACTIVE_POWER_IMPORT_{phase}"
				export_key = f"ACTIVE_POWER_EXPORT_{phase}"
				if import_key in payload and export_key in payload:
					payload[power_total_key] = payload[import_key] - payload[export_key]

		# Calculate total import if needed
		if "ACTIVE_POWER_IMPORT" not in payload:
			import_l1 = payload.get("ACTIVE_POWER_IMPORT_L1")
			import_l2 = payload.get("ACTIVE_POWER_IMPORT_L2")
			import_l3 = payload.get("ACTIVE_POWER_IMPORT_L3")
			if (
				import_l1 is not None
				and import_l2 is not None
				and import_l3 is not None
			):
				payload["ACTIVE_POWER_IMPORT"] = import_l1 + import_l2 + import_l3

		# Calculate total export if needed
		if "ACTIVE_POWER_EXPORT" not in payload:
			export_l1 = payload.get("ACTIVE_POWER_EXPORT_L1")
			export_l2 = payload.get("ACTIVE_POWER_EXPORT_L2")
			export_l3 = payload.get("ACTIVE_POWER_EXPORT_L3")
			if (
				export_l1 is not None
				and export_l2 is not None
				and export_l3 is not None
			):
				payload["ACTIVE_POWER_EXPORT"] = export_l1 + export_l2 + export_l3

		# Calculate total power if needed
		if "ACTIVE_POWER_TOTAL" not in payload:
			power_import = payload.get("ACTIVE_POWER_IMPORT")
			power_export = payload.get("ACTIVE_POWER_EXPORT")
			if power_import is not None and power_export is not None:
				payload["ACTIVE_POWER_TOTAL"] = power_import - power_export

		# Calculate total current if needed
		if "CURRENT_TOTAL" not in payload:
			current_l1 = payload.get("CURRENT_L1")
			current_l2 = payload.get("CURRENT_L2")
			current_l3 = payload.get("CURRENT_L3")
			if (
				current_l1 is not None
				and current_l2 is not None
				and current_l3 is not None
			):
				payload["CURRENT_TOTAL"] = current_l1 + current_l2 + current_l3

		# Calculate power factor for each phase and total
		valid_phases = []
		for phase in ("L1", "L2", "L3"):
			voltage = payload.get(f"VOLTAGE_{phase}")
			current = payload.get(f"CURRENT_{phase}")
			real_power = payload.get(f"ACTIVE_POWER_TOTAL_{phase}")

			if voltage is not None and current is not None and real_power is not None:
				valid_phases.append((phase, voltage, current, real_power))

		if len(valid_phases) not in (1, 3):
			logging.warning(
				"Unsupported number of valid power phases. Expected 1 or 3."
			)
			return  # Skip PF calculation entirely

		pf_total_numerator = 0.0
		pf_total_denominator = 0.0

		for phase, voltage, current, real_power in valid_phases:
			try:
				apparent_power = abs(voltage * current)
				if apparent_power < 1e-2:
					continue  # Skip unreliable values

				pf = real_power / apparent_power
				pf_clamped = max(min(pf, 1.0), -1.0)

				payload[f"POWER_FACTOR_{phase}"] = round(pf_clamped, 3)
				payload[f"POWER_FACTOR_{phase}_DIRECTION"] = (
					"lagging" if pf_clamped >= 0 else "leading"
				)

				pf_total_numerator += real_power
				pf_total_denominator += apparent_power

			except Exception as e:
				logging.warning(f"Skipping PF calc for {phase}: {e}")

		if pf_total_denominator >= 1e-2:
			total_pf = pf_total_numerator / pf_total_denominator
			total_pf_clamped = max(min(total_pf, 1.0), -1.0)
			payload["POWER_FACTOR_TOTAL"] = round(total_pf_clamped, 3)
			payload["POWER_FACTOR_TOTAL_DIRECTION"] = (
				"lagging" if total_pf_clamped >= 0 else "leading"
			)
