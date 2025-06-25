import logging
import os

import yaml

try:
	from lxml import etree

	USES_LXML = True
	print("running with lxml.etree")
except ImportError:
	import xml.etree.ElementTree as etree

	USES_LXML = False
	print("running with Python's xml.etree.ElementTree")

logging.basicConfig(
	level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)


class TelegramProcessor:
	def __init__(self) -> None:
		with open(
			os.path.join(os.path.dirname(__file__), "telegrams/default.yml"), "r"
		) as f:
			self._default_config = yaml.safe_load(f)
		self.available_telegrams = {}
		telegram_files = [
			f
			for f in os.listdir("telegrams")
			if f.endswith(".yml") and f != "default.yml"
		]

		for file_name in telegram_files:
			with open(os.path.join("telegrams", file_name), "r") as f:
				content = yaml.safe_load(f)
				if "info" in content and "id" in content["info"]:
					self.available_telegrams[content["info"]["id"]] = content

		self.config = self._default_config
		self.selected_telegram = None
		self._is_telegram_length_unique: dict[int, bool] = {}
		self._telegram_structure_hash: dict[int, str] = {}

	@classmethod
	def use_telegram(cls, telegram_id):
		self = cls()
		if telegram_id not in self.available_telegrams:
			avail_tel = "\n    ".join(self.available_telegrams.keys())
			raise ValueError(
				f"Telegram with id {telegram_id} not found!\n  "
				f"Available telegrams:\n    {avail_tel}"
			)
		self.selected_telegram = self.available_telegrams[telegram_id]
		self.config = self._default_config
		self.config.update(self.selected_telegram)
		for telegram in self.selected_telegram["telegrams"]:
			telegram_length = telegram["length"]
			self._is_telegram_length_unique[telegram_length] = (
				telegram_length not in self._is_telegram_length_unique.keys()
			)
			self._telegram_structure_hash[
				hash("".join([x["type"] for x in telegram["contents"]]))
			] = telegram["name"]
		return self

	def process_xml(self, xml):
		if USES_LXML:
			root = etree.fromstring(
				xml, parser=etree.XMLParser(recover=True, remove_comments=True)
			)
		else:
			root = etree.fromstring(xml, parser=etree.XMLParser())
		if root.tag != "Structure":
			raise ValueError("Invalid XML format")
		telegram_length = int(root.attrib.get("Qty"), 16)
		telegram_structure = self._get_structure(telegram_length, root)
		if telegram_structure is None:
			logging.error(
				f"Telegram with length {telegram_length} not found in selected telegram definitions. Unknown telegram:"
			)
			print(xml)
			raise ValueError("Unknown telegram")
		payload = {}
		for i, child in enumerate(root):
			try:
				payload.update(self._parse_element(i, child, telegram_structure))
			except ValueError as e:
				logging.error(f"Error parsing element {i}: {e}")
				print("Raw element:\n", etree.tostring(child).decode("utf-8"))
				print("\nRaw telegram:\n", xml)
				print("\nTelegram structure:\n", telegram_structure)
				raise e

		for transform in self.config.get("transformations", []):
			t = self._do_transform(payload, transform)
			if t is not None:
				payload.update(t)

		payload = self._generic_transform(payload)

		payload = {"name": telegram_structure["name"], "data": payload}

		return payload

	def _get_structure(self, telegram_length, root):
		# TODO: Support detection based on root contents
		# Length Method:
		# Find the first telegram definition that matches length of telegram
		tag_hash = hash("".join([x.tag for x in root]))
		for definition in self.selected_telegram["telegrams"]:
			if definition[
				"length"
			] == telegram_length and self._is_telegram_length_unique.get(
				telegram_length, False
			):
				return definition
			elif (
				self._telegram_structure_hash.get(tag_hash, None) == definition["name"]
			):
				return definition

	def _parse_element(self, position, child, telegram_structure):
		element = [
			e for e in telegram_structure["contents"] if e["position"] == position
		][0]
		if "type" not in element:
			raise ValueError("Element missing type in definition")
		if child.tag != element["type"]:
			raise ValueError(f"Expected tag {element['type']} but got {child.tag}")
		value = None
		if element["type"] == "OctetString":
			bytes_data = bytes.fromhex(child.attrib["Value"])
			value = bytes_data.decode("ascii")
		elif element["type"] in ["UInt32", "UInt16", "UInt8", "Enum"]:
			value = int(child.attrib["Value"], 16)
		elif element["type"] == "Boolean":
			value = child.attrib["Value"] == "True"
		else:
			print(
				f"Unsupported type {element['type']} with value {child.attrib['Value']}"
			)
		return {element["name"]: value}

	def _do_transform(self, payload, transform):
		transform_type = transform["type"]
		key_ = transform["key"]
		key_value = payload.get(key_)
		key = key_
		val = None
		if key_value is None:
			return
		if transform_type == "MULTIPLY":
			val = key_value * transform["value"]
		elif transform_type == "ADD":
			val = key_value + transform["value"]
		elif transform_type == "SUBTRACT":
			val = key_value - transform["value"]
		elif transform_type == "DIVIDE":
			val = key_value / transform["value"]
		elif transform_type == "REPLACE":
			val = transform["value"]
		elif transform_type == "TO_INTEGER":
			val = int(key_value)
		elif transform_type == "TO_STRING":
			val = str(key_value)
		elif transform_type == "TO_FLOAT":
			val = float(key_value)
		elif transform_type == "MULTIPLY_IF_KEY":
			operand = transform["operand"]
			transform_key = transform["transform_key"]
			key = transform_key
			val_to_transform = payload.get(transform_key)
			multiplier = transform["multiplier"]
			if operand == "GT":
				if key_value > transform["value"]:
					val = val_to_transform * multiplier
			elif operand == "GTE":
				if key_value >= transform["value"]:
					val = val_to_transform * multiplier
			elif operand == "LT":
				if key_value < transform["value"]:
					val = val_to_transform * multiplier
			elif operand == "LTE":
				if key_value <= transform["value"]:
					val = val_to_transform * multiplier
			elif operand == "EQ":
				if key_value == transform["value"]:
					val = val_to_transform * multiplier
			elif operand == "NEQ":
				if key_value != transform["value"]:
					val = val_to_transform * multiplier
			else:
				print(f"Unsupported operand {operand}")

		if val is not None:
			return {key: val}

	def _generic_transform(self, payload):
		# If no ACTIVE_POWER_TOTAL_L1, calculate it from ACTIVE_POWER_IMPORT_L1 and ACTIVE_POWER_EXPORT_L1
		if "ACTIVE_POWER_TOTAL_L1" not in payload:
			if (
				"ACTIVE_POWER_IMPORT_L1" in payload
				and "ACTIVE_POWER_EXPORT_L1" in payload
			):
				import_ = payload["ACTIVE_POWER_IMPORT_L1"]
				export_ = payload["ACTIVE_POWER_EXPORT_L1"]
				payload["ACTIVE_POWER_TOTAL_L1"] = import_ - export_

		# Do the same for L2 and L3
		if "ACTIVE_POWER_TOTAL_L2" not in payload:
			if (
				"ACTIVE_POWER_IMPORT_L2" in payload
				and "ACTIVE_POWER_EXPORT_L2" in payload
			):
				import_ = payload["ACTIVE_POWER_IMPORT_L2"]
				export_ = payload["ACTIVE_POWER_EXPORT_L2"]
				payload["ACTIVE_POWER_TOTAL_L2"] = import_ - export_

		if "ACTIVE_POWER_TOTAL_L3" not in payload:
			if (
				"ACTIVE_POWER_IMPORT_L3" in payload
				and "ACTIVE_POWER_EXPORT_L3" in payload
			):
				import_ = payload["ACTIVE_POWER_IMPORT_L3"]
				export_ = payload["ACTIVE_POWER_EXPORT_L3"]
				payload["ACTIVE_POWER_TOTAL_L3"] = import_ - export_

		# If not ACTIVE_POWER_IMPORT, calculate it from L1, L2, L3
		if "ACTIVE_POWER_IMPORT" not in payload:
			if all(
				key in payload
				for key in [
					"ACTIVE_POWER_IMPORT_L1",
					"ACTIVE_POWER_IMPORT_L2",
					"ACTIVE_POWER_IMPORT_L3",
				]
			):
				import_ = payload["ACTIVE_POWER_IMPORT_L1"]
				import_ += payload["ACTIVE_POWER_IMPORT_L2"]
				import_ += payload["ACTIVE_POWER_IMPORT_L3"]
				payload["ACTIVE_POWER_IMPORT"] = import_

		# If not ACTIVE_POWER_EXPORT, calculate it from L1, L2, L3
		if "ACTIVE_POWER_EXPORT" not in payload:
			if all(
				key in payload
				for key in [
					"ACTIVE_POWER_EXPORT_L1",
					"ACTIVE_POWER_EXPORT_L2",
					"ACTIVE_POWER_EXPORT_L3",
				]
			):
				export_ = payload["ACTIVE_POWER_EXPORT_L1"]
				export_ += payload["ACTIVE_POWER_EXPORT_L2"]
				export_ += payload["ACTIVE_POWER_EXPORT_L3"]
				payload["ACTIVE_POWER_EXPORT"] = export_

		# If not ACTIVE_POWER_TOTAL, calculate it from IMPORT and EXPORT
		if "ACTIVE_POWER_TOTAL" not in payload:
			if "ACTIVE_POWER_IMPORT" in payload and "ACTIVE_POWER_EXPORT" in payload:
				total = payload["ACTIVE_POWER_IMPORT"]
				total -= payload["ACTIVE_POWER_EXPORT"]
				payload["ACTIVE_POWER_TOTAL"] = total

		# If not CURRENT_TOTAL, calculate it from L1, L2, L3
		if "CURRENT_TOTAL" not in payload:
			if all(
				key in payload for key in ["CURRENT_L1", "CURRENT_L2", "CURRENT_L3"]
			):
				total = payload["CURRENT_L1"]
				total += payload["CURRENT_L2"]
				total += payload["CURRENT_L3"]
				payload["CURRENT_TOTAL"] = total

		return payload
